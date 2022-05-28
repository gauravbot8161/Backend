import gzip
import os.path
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, Optional
from xmlrpc.client import boolean

import httpx
from math import ceil
import ujson as json
from app import logger
from app.models import DataType


class TMDB:
    def __init__(self, api_key: str):
        if not os.path.exists("./cache/movie_ids.json"):
            self.export_data(DataType.movies)
        if not os.path.exists("./cache/tv_series_ids.json"):
            self.export_data(DataType.series)
        self.movie_export_data = json.load(
            open("./cache/movie_ids.json", "r", encoding="utf-8")
        )
        self.series_export_data = json.load(
            open("./cache/tv_series_ids.json", "r", encoding="utf-8")
        )
        self.client = httpx.Client(params={"api_key": api_key})
        self.config = self.get_server_config()
        self.image_base_url = self.config["images"]["secure_base_url"]

    def get_server_config(self) -> Dict[str, Any]:
        """Get the server config from the API

        Returns:
            dict: The server config
        """
        url = "https://api.themoviedb.org/3/configuration"
        response = self.client.get(url)
        return response.json()

    @staticmethod
    def export_data(data_type: DataType):
        date_str = (datetime.now() - timedelta(days=1)).strftime("%m_%d_%Y")
        type_name = "tv_series" if data_type == DataType.series else "movie"
        export_url = (
            f"http://files.tmdb.org/p/exports/{type_name}_ids_{date_str}.json.gz"
        )
        movie_lines = gzip.decompress(httpx.get(export_url).content).decode("utf-8").splitlines()
        length = len(movie_lines)
        with open(f"./cache/{type_name}_ids.json", "w+", encoding="utf-8") as w:
            w.write("[")
            for n, line in enumerate(movie_lines, 1):
                if (n == length):
                    w.write(line + "]")
                else:
                    w.write(line + ",")

    def get_episode_details(
        self, tmdb_id: int, episode_number: int, season_number: int = 1
    ) -> Dict[str, Any]:
        """Get the details of a specific episode from the API

        Args:
            tmdb_id (int): The TMDB ID of the episode
            episode_number (int): The episode number
            season_number (int, optional): The season number

        Returns:
            dict: The episode details
        """
        url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}"
        response = self.client.get(url)
        return response.json() if response.status_code == 200 else {}

    def find_media_id(
        self,
        title: str,
        data_type: DataType,
        use_api: bool = True,
        year: Optional[int] = None,
        adult: bool = False,
    ) -> Optional[int]:
        """The legacy way to get TMDB ID for a title
        it consumes a bit more memory and it's slower
        but the result is more accurate

        Args:
            title (str): The title of the movie / series
            data_type (DataType): The type of the title
            use_api (bool): Use API calls to get info
            year (int): Release Year of the media
            adult (bool): If the media is under adult category or not

        Returns:
            Optional[int]
        """
        from app.utils.data import clean_file_name

        title = title.lower().strip()
        original_title = title
        title = clean_file_name(title)
        if not title:
            logger.debug(f"The parsed title returned an empty string. Skipping...")
            logger.debug(f"Original Title: {original_title}")
            return None
        if use_api:
            logger.debug(f"Trying search using API for '{title}'")
            type_name = "tv" if data_type == DataType.series else "movie"
            resp = self.client.get(
                f"https://api.themoviedb.org/3/search/{type_name}",
                params={
                    "query": title,
                    "primary_release_year": year,
                    "include_adult": adult,
                    "page": 1,
                    "language": "en-US",
                },
            )
            if resp.status_code == 200:
                if data := resp.json()["results"]:
                    return data[0]["id"]
            else:
                logger.warning(
                    f"API search failed for '{title}' - The API said '{resp.json()['errors']}' with status code {resp.status_code}"
                )
                return
        else:
            data = (
                self.movie_export_data
                if data_type == DataType.movies
                else self.series_export_data
            )
            logger.debug(f"Trying search using key-value search for '{title}'")
            for each in data:
                if title == each.get("original_title", "").lower().strip():
                    return each["id"]
            logger.debug(f"Basic key-value search failed for '{title}'")
            max_ratio, match = 0, None
            matcher = SequenceMatcher(b=title)
            for each in data:
                matcher.set_seq1(each.get("original_title", "").lower().strip())
                ratio = matcher.ratio()
                if ratio > 0.99:
                    return each
                if ratio > max_ratio and ratio >= 0.85:
                    max_ratio = ratio
                    match = each
            if match:
                return match["id"]
            logger.debug(f"Advanced difflib search failed for '{title}'")

    def get_details(self, tmdb_id: int, data_type: DataType) -> Dict[str, Any]:
        """Get the details of a movie / series from the API

        Args:
            tmdb_id (int): The TMDB ID of the movie / series
            data_type (DataType): The type of the title

        Returns:
            dict: The details of the movie / series
        """
        type_name = "tv" if data_type == DataType.series else "movie"
        url = f"https://api.themoviedb.org/3/{type_name}/{tmdb_id}"
        params = {
            "include_image_language": "en",
            "append_to_response": "credits,images,external_ids",
        }
        response = self.client.get(url, params=params).json()
        length = len(response.get("seasons", []))
        append_seasons = []
        n_of_appends = ceil(length / 20)
        x = 0
        while x < n_of_appends:
            append_seasons.append("")
            for n in range((x * 20), ((x + 1) * 20)):
                append_seasons[x] = append_seasons[x] + "season/" + str(n) + ","
            append_seasons[x] = append_seasons[x][:-1]
            x += 1
        if type_name == "tv":
            for n, append_season in enumerate(append_seasons):
                params = {"append_to_response": append_season}
                tmp_response = self.client.get(url, params=params).json()
                season_keys = [k for k in tmp_response.keys() if "season/" in k]
                for k in season_keys:
                    response[k] = tmp_response[k]
        else:
            response = self.client.get(
                url,
                params={
                    "include_image_language": "en",
                    "append_to_response": "credits,images,external_ids",
                },
            ).json()
        # This limits the number of seasons to 17 seasons
        # More requests need to be made in the event of additional seasons
        # The data from those requests need to then be merged and returned
        return response
