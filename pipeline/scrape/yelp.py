"""Provides access to geographic locations using the Yelp Fusion API.
"""

# Standard library imports
import logging
import os
import time
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Tuple, Union

# Third-party imports
import requests
from shapely import MultiPolygon, Polygon

# Application imports
from pipeline.scrape.common import IPlacesProvider
from pipeline.utils.geometry import BoundingBox, convert_meters_to_degrees


class YelpPOICategories(Enum):
    """Enumerates all relevant categories for points of interest."""

    # Potential Indoor Points
    BAR = "bars"
    RESTAURANT = "restaurants"

    # Potential Outdoor Points
    AIRPORTS = "airports"
    APARTMENTS = "apartments"
    BIKE_SHARING_HUB = "bikesharing"
    BUS_STATION = "busstations"
    CONDO = "condominiums"
    DRUGSTORE = "drugstores"
    ELEMENTARY_SCHOOL = "elementaryschools"
    GROCERY = "grocery"
    HOTELS = "hotels"
    JUNIOR_OR_SENIOR_HIGH_SCHOOL = "highschools"
    LIBRARIES = "libraries"
    MEDICAL_CENTERS = "medcenters"
    METRO_STATION = "metrostations"
    OFFICE = "sharedofficespaces"
    PARK = "parks"
    POST_OFFICES = "postoffices"
    PHARMACY = "pharmacy"
    PRESCHOOL = "preschools"
    RECYCLING_CENTER = "recyclingcenter"
    SHARED_LIVING = "housingcooperatives"
    TRAIN_STATIONS = "trainstations"
    ZOOS = "zoos"


class YelpClient(IPlacesProvider):
    """A simple wrapper for the Yelp Fusion API."""

    MAX_NUM_PAGE_RESULTS: int = 50
    """The maximum number of results that can be returned on a single page of
    search results. The inclusive upper bound of the "limit" query parameter.
    """

    MAX_NUM_QUERY_RESULTS: int = 1_000
    """The maximum number of results that can be returned from a single query.
    """

    MAX_SEARCH_RADIUS_IN_METERS: int = 40_000
    """The maximum size of the suggested search radius in meters.
    Approximately equal to 25 miles.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Initializes a new instance of a `YelpClient`.

        Args:
            logger (`logging.Logger`): An instance of a Python
                standard logger.

        Raises:
            `RuntimeError` if an environment variable,
                `YELP_API_KEY`, is not found.

        Returns:
            `None`
        """
        try:
            self._api_key = os.environ["YELP_API_KEY"]
            self._logger = logger
        except KeyError as e:
            raise RuntimeError(
                "Failed to initialize YelpClient."
                f'Missing expected environment variable "{e}".'
            ) from None

    def find_places_in_bounding_box(
        self, box: BoundingBox, search_radius: float
    ) -> Tuple[List[Dict], List[Dict]]:
        """Locates all POIs within the bounding box.

        Args:
            box (`BoundingBox`): The bounding box.

            search_radius (`float`): The search radius, converted from
                meters to the larger of degrees longitude and latitude.

        Returns:
            ((`list` of `dict`, `list` of `dict`,)): A two-item tuple
                consisting of the list of retrieved places and a list
                of any errors that occurred, respectively.
        """
        # Initialize request URL and static params
        url = "https://api.yelp.com/v3/businesses/search"
        categories = ",".join(e.value for e in YelpPOICategories)
        limit = YelpClient.MAX_NUM_PAGE_RESULTS

        # Issue POI query for minimum bounding circle circumscribing each box
        # NOTE: Only integers are accepted for the radius.
        pois = []
        errors = []
        page_idx = 0
        while True:
            # Build request parameters and headers
            params = {
                "radius": search_radius,
                "categories": categories,
                "longitude": float(box.center.lon),
                "latitude": float(box.center.lat),
                "limit": limit,
                "offset": page_idx * limit,
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
            }

            # Send request and parse JSON response
            r = requests.get(url, headers=headers, params=params)
            data = r.json()

            # If error occurred, store information and exit processing for cell
            if not r.ok:
                self._logger.error(
                    "Failed to retrieve POI data through the Yelp API. "
                    f'Received a "{r.status_code}-{r.reason}" status code '
                    f'with the message "{r.text}".'
                )
                errors.append({"params": params, "error": data})
                return pois, errors

            # Otherwise, if number of POIs returned exceeds max, split
            # box and recursively issue HTTP requests
            if data["total"] > YelpClient.MAX_NUM_QUERY_RESULTS:
                sub_cells = box.split_along_axes(x_into=2, y_into=2)
                for sub in sub_cells:
                    sub_pois, sub_errs = self.find_places_in_bounding_box(
                        sub, search_radius / 2
                    )
                    pois.extend(sub_pois)
                    errors.extend(sub_errs)
                return pois, errors

            # Otherwise, extract business data from response body JSON
            page_pois = data.get("businesses", [])
            for poi in page_pois:
                pois.append(poi)

            # Determine total number of pages of data for query
            num_pages = (data["total"] // limit) + (
                1 if data["total"] % limit > 0 else 0
            )

            # Return POIs and errors if on last page
            if page_idx == num_pages - 1:
                return pois, errors

            # Otherwise, iterate page index and add delay before next request
            page_idx += 1
            time.sleep(0.5)

    def find_places_in_geography(
        self, geo: Union[Polygon, MultiPolygon]
    ) -> Tuple[List[Dict], List[Dict]]:
        """Locates all POIs with a review within the given geography.
        The Fusion API permits searching for POIs within a radius around
        a given point. Therefore, data is extracted by dividing the
        geography's bounding box into cells of equal size and then searching
        within the circular areas that circumscribe (i.e., perfectly enclose)
        those cells.

        To circumscribe a cell, the circle must have a radius that is
        one-half the length of the cell's diagonal (as derived from the
        Pythagorean Theorem). Let `side` equal the length of a cell's side.
        It follows that the radius is:

        ```
        radius = (√2/2) * side
        ```

        Yelp sets a cap on the radius search size, so after solving for `side`,
        it follows that cell sizes are restricted as follows:

        ```
        max_side = √2 * max_radius
        ```

        Therefore, the bounding box must be split into _at least_ the following
        number of cells along the x- and y- (i.e., longitude and latitude)
        directions to avoid having cells that are too big:

        ```
        min_num_splits = ceil(bounding_box_length / max_side)
        ```

        Finally, only 1,000 records may be fetched from a single query, with
        a maximum limit of 50 records per page of data, even if more businesses
        are available. Therefore, it is important to confirm that less than
        1,000 records are returned with a query to avoid missing data.

        Documentation:
        - ["Yelp API Reference | Search"](https://docs.developer.yelp.com/reference/v3_business_search)

        Args:
            geo (`Polygon` or `MultiPolygon`): The boundary.

        Returns:
            ((`list` of `dict`, `list` of `dict`,)): A two-item tuple
                consisting of the list of retrieved places and a list
                of any errors that occurred, respectively.
        """
        # Calculate bounding box for geography
        bbox: BoundingBox = BoundingBox.from_polygon(geo)

        # Calculate length of square circumscribed by circle with the max search radius
        max_side_meters = (2**0.5) * YelpClient.MAX_SEARCH_RADIUS_IN_METERS

        # Use heuristic to convert length from meters to degrees at box's lower latitude
        deg_lat, deg_lon = convert_meters_to_degrees(
            max_side_meters, bbox.bottom_left
        )

        # Take minimum value as side length (meters convert differently to lat and lon,
        # and we want to avoid going over max radius)
        max_side_degrees = min(deg_lat, deg_lon)

        # Divide box into grid of cells of approximately equal length and width
        # NOTE: Small size differences may exist due to rounding.
        cells: List[BoundingBox] = bbox.split_into_squares(
            size_in_degrees=Decimal(str(max_side_degrees))
        )

        # Locate POIs within each cell if it contains any part of geography
        pois = []
        errors = []
        for cell in cells:
            if cell.intersects_with(geo):
                # get the pois and errors list created by the
                # find_places_in_bounding_box function
                cell_pois, cell_errs = self.find_places_in_bounding_box(
                    box=cell, search_radius=YelpClient.MAX_SEARCH_RADIUS_IN_METERS
                )

                # Code for de-duping and restructuring POI dictionary
                unique_ids = set()
                unique_pois = []
                # I removed the new errors list and use the cell_errs list made by find_places_in_bounding_box
                cleaned_pois = []
                for poi in cell_pois:
                    id = poi.get("id")
                    if id not in unique_ids:
                        unique_ids.add(id)
                        unique_pois.append(poi)
                    else:
                        cell_errs.append("Duplicate ID found: {}".format(id))
                # Renaming varoables in original json for easy integration with other scrape files in clean.py
                for poi in unique_pois:
                    cleaned_poi = {}
                    closed = poi.get("is_closed")
                    if not closed:
                        cleaned_poi["id"] = poi.get("id")
                        cleaned_poi["name"] = poi.get("name")
                        cleaned_poi["categories"] = ", ".join(
                            [category["title"] for category in poi["categories"]]
                        )
                        cleaned_poi["latitude"] = poi.get("coordinates")["latitude"]
                        cleaned_poi["longitude"] = poi.get("coordinates")["longitude"]
                        cleaned_poi["display_address"] = ", ".join(
                            poi.get("location")["display_address"]
                        )
                        cleaned_pois.append(cleaned_poi)

                # back to original code in dev, except instead of adding the cell_pois returned
                # by the find_places_in_bounding_box, I add the newly cleaned pois
                pois.extend(cleaned_pois)
                errors.extend(cell_errs)

        return pois, errors
