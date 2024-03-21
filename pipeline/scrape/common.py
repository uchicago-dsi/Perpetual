"""Defines interfaces and common classes for scraping points of interest.
"""

# Standard library imports
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Union

# Third-party imports
from shapely import MultiPolygon, Polygon


class IPlacesProvider(ABC):
    """An abstract class for identifying points of interest (POI)."""

    @abstractmethod
    def find_places_in_geography(
        self, geo: Union[Polygon, MultiPolygon]
    ) -> Tuple[List[Dict], List[Dict]]:
        """Locates all POIs within the given geography.

        Args:
            geo (`Polygon` or `MultiPolygon`): The boundary.

        Returns:
            ((`list` of `dict`, `list` of `dict`,)): A two-item tuple
                consisting of the list of retrieved places and a list
                of any errors that occurred, respectively.
        """
        raise NotImplementedError
