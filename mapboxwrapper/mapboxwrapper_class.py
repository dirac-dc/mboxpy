from pathlib import Path
import re
from typing import List, Union
import json

import numpy as np
from colour import Color

from mapboxwrapper.defaults import PROJECT_ROOT

PREFIXED_TOGGLE_TEMPLATE_PATH = PROJECT_ROOT / "../templates/prefixed_toggle_template.txt"
TOGGLE_TEMPLATE_PATH = PROJECT_ROOT / "../templates/toggle_template.txt"
TEMPLATE_PATH = PROJECT_ROOT / "../templates/dark_template.html"
ACCESS_TOKEN_PATH = PROJECT_ROOT / "../.mapbox_access_token"
MARKER_NUMBER = 200

class MapBoxWrapper:
    SOURCE_NAME = 'all_data'
    FEATURE_KEYS = ['id_', 'geojson_type', 'colour', 'properties', 'array']
    LAYER_STYLES = {'Point':{'vector_type':'circle',
                             'paint':{'circle-color':['get', 'colour'],
                                      'circle-radius': ['case', ['has','circle-radius'],['get', 'circle-radius'], 3]}},
                    'LineString': {'vector_type': 'line',
                                   'paint': {'line-color': ['get', 'colour'], 'line-width': 1, 'line-opacity': 0.5},
                                   }
                    }
    LAYER_FILTERS = {'Point': ['==', '$type', 'Point'],
                    'LineString': ['==', '$type', 'LineString']}
    FILTER_DICT = {'Point':'Point',
                   'MultiPoint': 'Point',
                   'LineString':'LineString',
                   'MultiLineString':'LineString'}

    def __init__(self, access_token_path=ACCESS_TOKEN_PATH, template_path=TEMPLATE_PATH, markers=MARKER_NUMBER):
        self.markers = markers
        self.template_path = Path(template_path)
        self.template = self._read_template(access_token_path, self.template_path, self.markers, self.markers)
        self.features = []
        self.geojson_features = []
        self.geojson_filter_types = []
        self.layers = []

    def add_feature(self, feature: dict) -> None:

        if not (list(feature) == self.FEATURE_KEYS):
            raise AttributeError(f"Features dict must have keys {self.FEATURE_KEYS}.")

        self.features += [feature]
        geojson_feat = self._create_geojson_feature(**feature)
        self.geojson_features += [geojson_feat]
        self.geojson_filter_types += [self.FILTER_DICT[feature['geojson_type']]]

    def list_features(self):
        return self.geojson_features

    def output_html(self, output_path: Path, layer_property: str="", filters: List=[]) -> None:

        if filters:
            print('Warning: Properties in filters must be universal')
            if not isinstance(filters[0], list): # solves broadcasting problem with 1D array
                filters = [filters]

        self.geojson = self._create_geojson_from_features(self.geojson_features)
        self.source = self._create_source(self.SOURCE_NAME, self.geojson)
        self.template = self._add_source(self.template, self.source)

        self.layerids = []
        if layer_property:
            self.prop_dict = self.find_property_types(layer_property)

            for value_, types_ in self.prop_dict.items():
                for type_ in types_:
                    lid = "_".join([value_, type_.lower()])
                    self.layerids += [lid]
                    layer = self._create_layer(layer_id=lid,
                                               source_name=self.SOURCE_NAME,
                                               filter=["all",
                                                       ['==', layer_property, value_],
                                                       self.LAYER_FILTERS[type_]] + filters,
                                               **self.LAYER_STYLES[type_])
                    self.layers += [layer]
                    self.template = self._add_layer(self.template, layer)

            self.template = self._add_toggle_script(self.template,
                                                    layerids=self.layerids,
                                                    prefixes=self.prop_dict.keys())
        else:
            for k, type_ in enumerate(np.unique(self.geojson_filter_types)):
                lid = type_ + '_layer'
                self.layerids += [lid]
                layer = self._create_layer(layer_id=lid,
                                           source_name=self.SOURCE_NAME,
                                           filter=["all", self.LAYER_FILTERS[type_]]+ filters,
                                           **self.LAYER_STYLES[type_])
                self.layers += [layer]
                self.template = self._add_layer(self.template, layer)

            self.template = self._add_toggle_script(self.template,
                                                    layerids=self.layerids)

        self._write_filled_template(output_path, self.template)
        print(f"Output written at {output_path}.")

    def find_property_types(self, property_):
        property_dict = {}
        for feat in self.features:
            if feat['properties'].get(property_, False):
                prop = feat['properties'][property_]
                if prop not in property_dict:
                    property_dict[prop] = [self.FILTER_DICT[feat['geojson_type']]]
                else:
                    property_dict[prop] += [self.FILTER_DICT[feat['geojson_type']]]
        return {key:np.unique(item) for key, item in property_dict.items()}




    def _create_geojson_feature(self,
                                array: Union[np.array, list],
                                id_: str,
                                geojson_type: str,
                                properties: dict,
                                colour=Color("lime").get_hex()):
        """
        Param
        _____
        latlon:
            2D numpy array of [N, [Longitude, Latitude]] of N samples. NOTE THE LONLAT ORDER!
        type_:
            Option["Point", "LineString"]
        """
        all_props = {**{'colour':colour}, **properties}

        lonlat = array if isinstance(array, list) else array.tolist()

        return ("""{'type': 'Feature',\n'id': '%s',\n'properties': %s,""" % (id_, all_props)
                + """\n'geometry': {\n\t'type': '%s',\n\t'coordinates':""" % (geojson_type)
                + str(lonlat)
                + "}\n}")

    def _create_geojson_from_features(self, features: List) -> str:
        """
        Param
        _____
        latlon:
            2D numpy array of [N, [Latitide, Longitude]] of N samples.
        """

        start_str = """{'type': 'geojson'\n, 'data': {'type': 'FeatureCollection',\n'features': ["""

        end_str = "]}}"

        start_str += ",\n".join(features)

        return start_str + end_str

    def _create_source(self, name: str, geojson: str) -> str:
        self.source_name = name
        return """map.addSource('%s', %s);
        """ % (name, geojson)

    def _create_layer(self,
                      layer_id: str,
                      vector_type: str,
                      source_name: str,
                      paint: dict = None,
                      filter: List = None) -> str:

        filter_placeholder = ''

        if filter:
            filter_placeholder = ",\n\t\t'filter': "+str(filter)

        return """map.addLayer({'id': '%s',
        'type': '%s',
        'source': '%s',
        'paint': %s,
        'layout': {'visibility': 'visible'}%s
        });
        """ % (layer_id, vector_type, source_name, str(paint), filter_placeholder)

    def _read_template(self,
                       access_token_path: Path,
                       template_path: str, source_markers:int=20,
                       layer_markers:int=20) -> str:

        f = open(template_path, "r")
        template = "".join(f.readlines())
        f.close()

        f = open(access_token_path, "r")
        mb_token = f.readline()
        f.close()

        template = re.sub('__MAPBOX_ACCESS_TOKEN__', mb_token, template)
        template = re.sub('__SOURCEMARKERS__', '__FILLINSOURCE__\n\n' * source_markers, template)
        template = re.sub('__LAYERMARKERS__', '__FILLINLAYER__\n\n' * layer_markers, template)
        return template

    def _write_filled_template(self, path: Path, filled_template: str) -> None:
        filled_template = filled_template.replace("__FILLINSOURCE__\n\n", "").replace("__FILLINLAYER__\n\n", "")
        self.html = filled_template
        w = open(path, "w+")
        w.writelines(filled_template)
        w.close()

    def _add_source(self, template: str, source_str: str) -> str:
        return re.sub('__FILLINSOURCE__', source_str, template, count=1)

    def _add_layer(self, template: str, layer_str: str) -> str:
        return re.sub('__FILLINLAYER__', layer_str, template, count=1)

    def _add_toggle_script(self, template: str, layerids: list, prefixes=None):

        togg_path = PREFIXED_TOGGLE_TEMPLATE_PATH if prefixes else TOGGLE_TEMPLATE_PATH

        f = open(togg_path, "r")
        toggle_script = "".join(f.readlines())
        f.close()

        toggle_script = toggle_script.replace("__FILLINLAYERIDS__", str(layerids))
        toggle_script = toggle_script.replace("__FILLINLAYERPROPERTY__", str(list(prefixes)))

        return template.replace("__FILLINTOGGLESCRIPT__", toggle_script)

