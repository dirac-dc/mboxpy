from pathlib import Path
import re
from typing import List, Union
import logging

import numpy as np
from colour import Color
import folium

from mapboxwrapper.defaults import PROJECT_ROOT

PREFIXED_TOGGLE_TEMPLATE_PATH = PROJECT_ROOT / "../templates/prefixed_toggle_template.txt"
TOGGLE_TEMPLATE_PATH = PROJECT_ROOT / "../templates/toggle_template.txt"
TEMPLATE_PATH = PROJECT_ROOT / "../templates/dark_template.html"
ACCESS_TOKEN_PATH = PROJECT_ROOT / "../.mapbox_access_token"
MARKER_NUMBER = 200
MARGIN = 0.01

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class MapBoxWrapper:
    '''Creates a mapbox map html page from a set of coordinates in [longitude, latitude] order.
    It does this by filling in an appropriate template with the correct strings.
    '''

    SOURCE_NAME = 'all_data'
    FEATURE_KEYS = ['id_', 'geojson_type', 'colour', 'properties', 'array']
    LAYER_STYLES = {'Point': {'vector_type': 'circle',
                              'paint': {'circle-color': ['get', 'colour'],
                                        'circle-radius': ['case',
                                                          ['has', 'circle-radius'],
                                                          ['get', 'circle-radius'],
                                                          3]
                                        }
                              },
                    'LineString': {'vector_type': 'line',
                                   'paint': {'line-color': ['get', 'colour'], 'line-width': 1, 'line-opacity': 0.5},
                                   }
                    }
    LAYER_FILTERS = {'Point': ['==', '$type', 'Point'],
                     'LineString': ['==', '$type', 'LineString']}
    FILTER_DICT = {'Point': 'Point',
                   'MultiPoint': 'Point',
                   'LineString': 'LineString',
                   'MultiLineString': 'LineString'}

    def __init__(self, access_token_path=ACCESS_TOKEN_PATH, template_path=TEMPLATE_PATH, markers=MARKER_NUMBER):
        self.markers = markers
        self.template_path = Path(template_path)
        self.template = self._read_template(access_token_path, self.template_path, self.markers, self.markers)
        self.features = []
        self.geojson_features = []
        self.geojson_filter_types = []
        self.layers = []
        self.layerids = []
        self.all_coords = []

    def add_feature(self, feature: dict) -> None:
        '''Collects features and creates geo_json string snippets from them.'''

        if not (list(feature) == self.FEATURE_KEYS):
            raise AttributeError(f"Features dict must have keys {self.FEATURE_KEYS}.")

        coord = np.ravel(feature['array']).tolist() if (feature['geojson_type'] != 'Point') else feature['array']
        self.all_coords += coord
        self.features += [feature]
        geojson_feat = self._create_geojson_feature(**feature)
        self.geojson_features += [geojson_feat]
        self.geojson_filter_types += [self.FILTER_DICT[feature['geojson_type']]]

    def list_features(self):
        return self.features

    def output_html(self, output_path: Path, layer_property: str = "", filters: list = ()) -> None:

        self.template = self._add_center_and_bounds(self.template)

        if filters:
            logger.warning(('Warning: Properties in filters must be universal '
                            'i.e. the property must be defined in every feature.'))

            if not isinstance(filters[0], list):  # solves broadcasting problem with 1D array
                filters = [filters]

        self.geojson = self._create_geojson_from_features(self.geojson_features)
        self.source = self._create_source(self.SOURCE_NAME, self.geojson)
        self.template = self._add_source(self.template, self.source)

        if layer_property:
            self.prop_dict = self._find_property_types(layer_property)

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
                                           filter=["all", self.LAYER_FILTERS[type_]] + filters,
                                           **self.LAYER_STYLES[type_])
                self.layers += [layer]
                self.template = self._add_layer(self.template, layer)

            self.template = self._add_toggle_script(self.template,
                                                    layerids=self.layerids)

        self._write_filled_template(output_path, self.template)
        print(f"Output written at {output_path}.")

    def _find_property_types(self, property_):
        '''Returns a dictionary of each layer property value with the types (point or line) it contains.

        Mapbox can only create layers of single type so if a wanted layer is made of different types, we
        have to know what layers go with each layer.'''
        property_dict = {}
        for feat in self.features:
            if feat['properties'].get(property_, False):
                prop = feat['properties'][property_]
                if prop not in property_dict:
                    property_dict[prop] = [self.FILTER_DICT[feat['geojson_type']]]
                else:
                    property_dict[prop] += [self.FILTER_DICT[feat['geojson_type']]]
        return {key: np.unique(item) for key, item in property_dict.items()}

    def _create_geojson_feature(self,
                                array: Union[np.array, list],
                                id_: str,
                                geojson_type: str,
                                properties: dict,
                                colour=Color("lime").get_hex()):
        """Returns geo_json string snippet to be added to template.

        Param
        _____
        array:
            2D numpy array of [N, [Longitude, Latitude]] of N samples. NOTE THE LONLAT ORDER!
        id_:
            feature id
        geojson_type:
            Option["Point", "LineString", "MultiPoint", "MultiLineString"]. Look up GeoType conventions on Google.
        properties:
            Freely defined dictionary of the properties you wish each feature to have. If you wish to create certain
            layers from these feature create a property such as 'node' appropriately and state this your layer_property
            in the `ouput_html` function.
        colour:
            The colour that will be fetched for the feature during rendering.
        """
        all_props = {**{'colour': colour}, **properties}

        lonlat = array if isinstance(array, list) else array.tolist()

        return ("""{'type': 'Feature',\n'id': '%s',\n'properties': %s,""" % (id_, all_props)
                + """\n'geometry': {\n\t'type': '%s',\n\t'coordinates':""" % (geojson_type)
                + str(lonlat)
                + "}\n}")

    def _create_geojson_from_features(self, features: list) -> str:
        """Assimilates a list of geo_json strings into one GeoJson string definition."""

        start_str = """{'type': 'geojson'\n, 'data': {'type': 'FeatureCollection',\n'features': ["""

        end_str = "]}}"

        start_str += ",\n".join(features)

        return start_str + end_str

    def _create_source(self, name: str, geojson: str) -> str:
        '''Creates a source string definition.'''
        self.source_name = name
        return """map.addSource('%s', %s);
        """ % (name, geojson)

    def _create_layer(self,
                      layer_id: str,
                      vector_type: str,
                      source_name: str,
                      paint: dict = None,
                      filter: List = None) -> str:

        '''Creates a mapbox layer. Ensures each layer is of only one type (point or line)'''

        filter_placeholder = ''

        if filter:
            filter_placeholder = ",\n\t\t'filter': " + str(filter)

        return """map.addLayer({'id': '%s',
        'type': '%s',
        'source': '%s',
        'paint': %s,
        'layout': {'visibility': 'visible'}%s
        });
        """ % (layer_id, vector_type, source_name, str(paint), filter_placeholder)

    def _read_template(self,
                       access_token_path: Path,
                       template_path: str, source_markers: int = 20,
                       layer_markers: int = 20) -> str:

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
        '''Fills in template with source string definition'''
        return re.sub('__FILLINSOURCE__', source_str, template, count=1)

    def _add_layer(self, template: str, layer_str: str) -> str:
        '''Fills in template with layer string definition'''
        return re.sub('__FILLINLAYER__', layer_str, template, count=1)

    def _add_toggle_script(self, template: str, layerids: list, prefixes=None):
        '''Adds javascript to create clickable toggles to the template'''

        togg_path = PREFIXED_TOGGLE_TEMPLATE_PATH if prefixes else TOGGLE_TEMPLATE_PATH

        f = open(togg_path, "r")
        toggle_script = "".join(f.readlines())
        f.close()

        toggle_script = toggle_script.replace("__FILLINLAYERIDS__", str(layerids))
        toggle_script = toggle_script.replace("__FILLINLAYERPROPERTY__", str(list(prefixes)))

        return template.replace("__FILLINTOGGLESCRIPT__", toggle_script)

    def _find_center_and_bounds(self):
        '''Finds the center for the map and the bounding rectangle [SW coord, NEcoord] of the coordinates.
        It exploits folium to do this but note that folium takes in coordinates in [latitiude, longitude] order
        so switches have to be made.
        '''
        coords = np.array(self.all_coords).reshape(len(self.all_coords) // 2, 2)
        center = coords.mean(axis=0).tolist()
        m = folium.Map(location=list(reversed(center)))
        for coord in coords[:, ::-1]:
            folium.Marker(location=coord).add_to(m)
        rb = m.get_bounds()
        return center, [[rb[0][1] - MARGIN, rb[0][0] - MARGIN], [rb[1][1] + MARGIN, rb[1][0] + MARGIN]]

    def _add_center_and_bounds(self, template: str):
        '''Adds center and bounds appropriately to template.'''
        center, bounds = self._find_center_and_bounds()
        return template.replace("__FILLINCENTER__", str(center)).replace("__FILLINBOUNDS__", str(bounds))
