#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Module: NetflixSession
# Created on: 13.01.2017

import sys
import os
import base64
import time
import urllib
import json
import requests
try:
   import cPickle as pickle
except:
   import pickle
from bs4 import BeautifulSoup
from utils import strip_tags
from utils import noop

class NetflixSession:
    """Helps with login/session management of Netflix users & API data fetching"""

    base_url = 'https://www.netflix.com'
    """str: Secure Netflix url"""

    urls = {
        'login': '/login',
        'browse': '/browse',
        'video_list_ids': '/warmer',
        'shakti': '/pathEvaluator',
        'profiles':  '/profiles',
        'switch_profiles': '/profiles/switch',
        'adult_pin': '/pin/service',
        'metadata': '/metadata',
        'set_video_rating': '/setVideoRating',
        'update_my_list': '/playlistop'
    }
    """:obj:`dict` of :obj:`str` List of all static endpoints for HTML/JSON POST/GET requests"""

    video_list_keys = ['user', 'genres', 'recommendations']
    """:obj:`list` of :obj:`str` Divide the users video lists into 3 different categories (for easier digestion)"""

    profiles = {}
    """:obj:`dict`
        Dict of user profiles, user id is the key:

        "72ERT45...": {
            "profileName": "username",
            "avatar": "http://..../avatar.png",
            "id": "72ERT45...",
            "isAccountOwner": False,
            "isActive": True,
            "isFirstUse": False
        }
    """

    user_data = {}
    """:obj:`dict`
        dict of user data (used for authentication):

        {
            "guid": "72ERT45...",
            "authURL": "145637....",
            "countryOfSignup": "DE",
            "emailAddress": "foo@..",
            "gpsModel": "harris",
            "isAdultVerified": True,
            "isInFreeTrial": False,
            "isKids": False,
            "isTestAccount": False,
            "numProfiles": 5,
            "pinEnabled": True
        }
    """

    api_data = {}
    """:obj:`dict`
        dict of api data (used to build up the api urls):

        {
            "API_BASE_URL": "/shakti",
            "API_ROOT": "https://www.netflix.com/api",
            "BUILD_IDENTIFIER": "113b89c9", "
            ICHNAEA_ROOT": "/ichnaea"
        }
    """

    esn = ''
    """str: Widevine esn, something like: NFCDCH-MC-D7D6F54LOPY8J416T72MQXX3RD20ME"""

    def __init__(self, cookie_path, data_path, verify_ssl=True, log_fn=noop):
        """Stores the cookie path for later use & instanciates a requests
           session with a proper user agent & stored cookies/data if available

        Parameters
        ----------
        cookie_path : :obj:`str`
            Cookie location

        data_path : :obj:`str`
            User data cache location

        log_fn : :obj:`fn`
             optional log function
        """
        self.cookie_path = cookie_path
        self.data_path = data_path
        self.verify_ssl = verify_ssl
        self.log = log_fn

        # start session, fake chrome (so that we get a proper widevine esn) & enable gzip
        self.session = requests.session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36',
            'Accept-Encoding': 'gzip, deflate'
        })

    def parse_login_form_fields (self, form_soup):
        """Fetches all the inputfields from the login form, so that we
           can build a request with all the fields needed besides the known email & password ones

        Parameters
        ----------
        form_soup : :obj:`BeautifulSoup`
            Instance of an BeautifulSoup documet or node containing the login form

        Returns
        -------
            :obj:`dict` of :obj:`str`
                Dictionary of all input fields with their name as the key & the default
                value from the form field
        """
        login_input_fields = {}
        login_inputs = form_soup.find_all('input')
        # gather all form fields, set an empty string as the default value
        for item in login_inputs:
            keys = dict(item.attrs).keys()
            if 'name' in keys and 'value' not in keys:
                login_input_fields[item['name']] = ''
            elif 'name' in keys and 'value' in keys:
                login_input_fields[item['name']] = item['value']
        return login_input_fields

    def extract_inline_netflix_page_data (self, page_soup):
        """Extracts all <script/> tags from the given document and parses the contents of each one of `em.
        The contents of the parsable tags looks something like this:

            <script>window.netflix = window.netflix || {} ;
                    netflix.notification = {"constants":{"sessionLength":30,"ownerToken":"ZDD...};</script>

        So we´re extracting every JavaScript object contained in the `netflix.x = {};` variable,
        strip all html tags, unescape the whole thing & finally parse the resulting serialized JSON from this
        operations. Errors are expected, as not all <script/> tags contained in the page follow these pattern,
        but the ones we need do, so we´re just catching any errors and applying a noop() function in case this happens,
        as we´re not interested in those.

        Note: Yes this is ugly & I´d like to avoid doing this, but Netflix leaves us no other choice,
            as there are simply no api endpoints for the data, we need to extract them from HTML,
            or better, JavaScript as we´re parsing the contents of <script/> tags

        Parameters
        ----------
        page_soup : :obj:`BeautifulSoup`
            Instance of an BeautifulSoup document or node containing the complete page contents

        Returns
        -------
            :obj:`list` of :obj:`dict`
                List of all the serialized data pulled out of the pagws <script/> tags
        """
        inline_data = [];
        data_scripts = page_soup.find_all('script', attrs={'src': None});
        for script in data_scripts:
            # ugly part: try to parse the data & don't care about errors (as they will be some)
            try:
                # find the first occurance of the 'netflix.' string, assigning the contents to a global js var
                str_index = str(script).find('netflix.')
                # filter out the contents between the 'netflix.x =' & ';<script>'
                stripped_data = str(script)[str_index:][(str(script)[str_index:].find('= ') + 2):].replace(';</script>', '').strip()
                # unescape the contents as they contain characters a JSON parser chokes up upon
                unescaped_data = stripped_data.decode('string_escape')
                # strip all the HTML tags within the strings a JSON parser chokes up upon them
                transformed_data = strip_tags(unescaped_data)
                # parse the contents with a regular JSON parser, as they should be in a shape that ot actually works
                try:
                    parsed_data = json.loads(transformed_data)
                    inline_data.append(parsed_data)
                except ValueError, e:
                    noop()
            except TypeError, e:
                noop()

        return inline_data;

    def _parse_user_data (self, netflix_page_data):
        """Parse out the user data from the big chunk of dicts we got from
           parsing the JSON-ish data from the netflix homepage

        Parameters
        ----------
        netflix_page_data : :obj:`list`
            List of all the JSON-ish data that has been extracted from the Netflix homepage
            see: extract_inline_netflix_page_data

        Returns
        -------
            :obj:`dict` of :obj:`str`

            {
                "guid": "72ERT45...",
                "authURL": "145637....",
                "countryOfSignup": "DE",
                "emailAddress": "foo@..",
                "gpsModel": "harris",
                "isAdultVerified": True,
                "isInFreeTrial": False,
                "isKids": False,
                "isTestAccount": False,
                "numProfiles": 5,
                "pinEnabled": True
            }
        """
        user_data = {};
        important_fields = [
            'authURL',
            'countryOfSignup',
            'emailAddress',
            'gpsModel',
            'guid',
            'isAdultVerified',
            'isInFreeTrial',
            'isKids',
            'isTestAccount',
            'numProfiles',
            'pinEnabled'
        ]
        for item in netflix_page_data:
            if 'models' in dict(item).keys():
                for important_field in important_fields:
                    user_data.update({important_field: item['models']['userInfo']['data'][important_field]})
        return user_data

    def _parse_profile_data (self, netflix_page_data):
        """Parse out the profile data from the big chunk of dicts we got from
           parsing the JSON-ish data from the netflix homepage

        Parameters
        ----------
        netflix_page_data : :obj:`list`
            List of all the JSON-ish data that has been extracted from the Netflix homepage
            see: extract_inline_netflix_page_data

        Returns
        -------
            :obj:`dict` of :obj:`dict

            {
                "72ERT45...": {
                    "profileName": "username",
                    "avatar": "http://..../avatar.png",
                    "id": "72ERT45...",
                    "isAccountOwner": False,
                    "isActive": True,
                    "isFirstUse": False
                }
            }
        """
        profiles = {};
        important_fields = [
            'profileName',
            'isActive',
            'isFirstUse',
            'isAccountOwner'
        ]
        # TODO: get rid of this christmas tree of doom
        for item in netflix_page_data:
            if 'profiles' in dict(item).keys():
                for profile_id in item['profiles']:
                    if self._is_size_key(key=profile_id) == False:
                        profile = {'id': profile_id}
                        for important_field in important_fields:
                            profile.update({important_field: item['profiles'][profile_id]['summary'][important_field]})
                        profile.update({'avatar': item['avatars']['nf'][item['profiles'][profile_id]['summary']['avatarName']]['images']['byWidth']['320']['value']})
                        profiles.update({profile_id: profile})

        return profiles

    def _parse_api_base_data (self, netflix_page_data):
        """Parse out the api url data from the big chunk of dicts we got from
           parsing the JSOn-ish data from the netflix homepage

        Parameters
        ----------
        netflix_page_data : :obj:`list`
            List of all the JSON-ish data that has been extracted from the Netflix homepage
            see: extract_inline_netflix_page_data

        Returns
        -------
            :obj:`dict` of :obj:`str

            {
                "API_BASE_URL": "/shakti",
                "API_ROOT": "https://www.netflix.com/api",
                "BUILD_IDENTIFIER": "113b89c9", "
                ICHNAEA_ROOT": "/ichnaea"
            }
        """
        api_data = {};
        important_fields = [
            'API_BASE_URL',
            'API_ROOT',
            'BUILD_IDENTIFIER',
            'ICHNAEA_ROOT'
        ]
        for item in netflix_page_data:
            if 'models' in dict(item).keys():
                for important_field in important_fields:
                    api_data.update({important_field: item['models']['serverDefs']['data'][important_field]})
        return api_data

    def _parse_esn_data (self, netflix_page_data):
        """Parse out the esn id data from the big chunk of dicts we got from
           parsing the JSOn-ish data from the netflix homepage

        Parameters
        ----------
        netflix_page_data : :obj:`list`
            List of all the JSON-ish data that has been extracted from the Netflix homepage
            see: extract_inline_netflix_page_data

        Returns
        -------
            :obj:`str` of :obj:`str
            Widevine esn, something like: NFCDCH-MC-D7D6F54LOPY8J416T72MQXX3RD20ME
        """
        esn = '';
        for item in netflix_page_data:
            if 'models' in dict(item).keys():
                esn = item['models']['esnGeneratorModel']['data']['esn']
        return esn

    def _parse_page_contents (self, page_soup):
        """Call all the parsers we need to extract all the session relevant data from the HTML page
           Directly assigns it to the NetflixSession instance

        Parameters
        ----------
        page_soup : :obj:`BeautifulSoup`
            Instance of an BeautifulSoup document or node containing the complete page contents
        """
        netflix_page_data = self.extract_inline_netflix_page_data(page_soup=page_soup)
        self.user_data = self._parse_user_data(netflix_page_data=netflix_page_data)
        self.esn = self._parse_esn_data(netflix_page_data=netflix_page_data)
        self.api_data = self._parse_api_base_data(netflix_page_data=netflix_page_data)
        self.profiles = self._parse_profile_data(netflix_page_data=netflix_page_data)

    def is_logged_in (self, account):
        """Determines if a user is already logged in (with a valid cookie),
           by fetching the index page with the current cookie & checking for the
           `membership status` user data

        Parameters
        ----------
        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property

        Returns
        -------
        bool
            User is already logged in (e.g. Cookie is valid) or not
        """
        is_logged_in = False
        # load cookies
        account_hash = self._generate_account_hash(account=account)
        if self._load_cookies(filename=self.cookie_path + '_' + account_hash) == False:
            return False
        if self._load_data(filename=self.data_path + '_' + account_hash) == False:
            # load the profiles page (to verify the user)
            response = self.session.get(self._get_document_url_for(component='profiles'), verify=self.verify_ssl)

            # parse out the needed inline information
            page_soup = BeautifulSoup(response.text)
            page_data = self.extract_inline_netflix_page_data(page_soup=page_soup)
            self._parse_page_contents(page_soup=page_soup)

            # check if the cookie is still valid
            for item in page_data:
                if 'profilesList' in dict(item).keys():
                    if item['profilesList']['summary']['length'] >= 1:
                        is_logged_in = True
            return is_logged_in
        return True

    def logout (self):
        """Delete all cookies and session data

        Parameters
        ----------
        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property

        """
        self._delete_cookies(path=self.cookie_path)
        self._delete_data(path=self.data_path)

    def login (self, account):
        """Try to log in a user with its credentials & stores the cookies if the action is successfull

           Note: It fetches the HTML of the login page to extract the fields of the login form,
           again, this is dirty, but as the fields & their values coudl change at any time, this
           should be the most reliable way of retrieving the information

        Parameters
        ----------
        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property

        Returns
        -------
        bool
            User could be logged in or not
        """
        response = self.session.get(self._get_document_url_for(component='login'), verify=self.verify_ssl)
        if response.status_code != 200:
            return False;

        # collect all the login fields & their contents and add the user credentials
        page_soup = BeautifulSoup(response.text)
        login_form = page_soup.find(attrs={'class' : 'ui-label-text'}).findPrevious('form')
        login_payload = self.parse_login_form_fields(form_soup=login_form)
        if 'email' in login_payload:
            login_payload['email'] = account['email']
        if 'emailOrPhoneNumber' in login_payload:
            login_payload['emailOrPhoneNumber'] = account['email']
        login_payload['password'] = account['password']

        # perform the login
        login_response = self.session.post(self._get_document_url_for(component='login'), data=login_payload, verify=self.verify_ssl)
        login_soup = BeautifulSoup(login_response.text)

        # we know that the login was successfull if we find an HTML element with the class of 'profile-name'
        if login_soup.find(attrs={'class' : 'profile-name'}) or login_soup.find(attrs={'class' : 'profile-icon'}):
            # parse the needed inline information & store cookies for later requests
            self._parse_page_contents(page_soup=login_soup)
            account_hash = self._generate_account_hash(account=account)
            self._save_cookies(filename=self.cookie_path + '_' + account_hash)
            self._save_data(filename=self.data_path + '_' + account_hash)
            return True
        else:
            return False

    def switch_profile (self, profile_id, account):
        """Switch the user profile based on a given profile id

        Note: All available profiles & their ids can be found in the ´profiles´ property after a successfull login

        Parameters
        ----------
        profile_id : :obj:`str`
            User profile id

        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property

        Returns
        -------
        bool
            User could be switched or not
        """
        payload = {
            'switchProfileGuid': profile_id,
            '_': int(time.time()),
            'authURL': self.user_data['authURL']
        }

        response = self.session.get(self._get_api_url_for(component='switch_profiles'), params=payload, verify=self.verify_ssl);
        if response.status_code != 200:
            return False

        # fetch the index page again, so that we can fetch the corresponding user data
        browse_response = self.session.get(self._get_document_url_for(component='browse'), verify=self.verify_ssl)
        browse_soup = BeautifulSoup(browse_response.text)
        self._parse_page_contents(page_soup=browse_soup)
        account_hash = self._generate_account_hash(account=account)
        self._save_data(filename=self.data_path + '_' + account_hash)
        return True

    def send_adult_pin (self, pin):
        """Send the adult pin to Netflix in case an adult rated video requests it

        Note: Once entered, it should last for the complete session (Not so sure about this)

        Parameters
        ----------
        pin : :obj:`str`
            The users adult pin

        Returns
        -------
        bool
            Pin was accepted or not
        or
        :obj:`dict` of :obj:`str`
            Api call error
        """
        payload = {
            'pin': pin,
            'authURL': self.user_data['authURL']
        }
        url = self._get_api_url_for(component='adult_pin')
        response = self.session.get(url, params=payload, verify=self.verify_ssl);
        pin_response = self._process_response(response=response, component=url)
        keys = pin_response.keys()
        if 'success' in keys:
            return True
        if 'error' in keys:
            return pin_response
        return False

    def add_to_list (self, video_id):
        """Adds a video to "my list" on Netflix

        Parameters
        ----------
        video_id : :obj:`str`
            ID of th show/video/movie to be added

        Returns
        -------
        bool
            Adding was successfull
        """
        return self._update_my_list(video_id=video_id, operation='add')

    def remove_from_list (self, video_id):
        """Removes a video from "my list" on Netflix

        Parameters
        ----------
        video_id : :obj:`str`
            ID of th show/video/movie to be removed

        Returns
        -------
        bool
            Removing was successfull
        """
        return self._update_my_list(video_id=video_id, operation='remove')

    def rate_video (self, video_id, rating):
        """Rate a video on Netflix

        Parameters
        ----------
        video_id : :obj:`str`
            ID of th show/video/movie to be rated

        rating : :obj:`int`
            Rating, must be between 0 & 10

        Returns
        -------
        bool
            Rating successfull or not
        """

        # dirty rating validation
        ratun = int(rating)
        if rating > 10 or rating < 0:
            return False

        # In opposition to Kodi, Netflix uses a rating from 0 to in 0.5 steps
        if rating != 0:
            rating = rating / 2

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/javascript, */*',
        }

        params = {
            'titleid': video_id,
            'rating': rating
        }

        payload = json.dumps({
            'authURL': self.user_data['authURL']
        })

        response = self.session.post(self._get_api_url_for(component='set_video_rating'), params=params, headers=headers, data=payload, verify=self.verify_ssl)
        return response.status_code == 200

    def parse_video_list_ids (self, response_data):
        """Parse the list of video ids e.g. rip out the parts we need

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the ´fetch_video_list_ids´ call

        Returns
        -------
        :obj:`dict` of :obj:`dict`
            Video list ids in the format:

            {
                "genres": {
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568367": {
                        "displayName": "US-Serien",
                        "id": "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568367",
                        "index": 3,
                        "name": "genre",
                        "size": 38
                    },
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568368": {
                        "displayName": ...
                    },
                },
                "user": {
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568364": {
                        "displayName": "Meine Liste",
                        "id": "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568364",
                        "index": 0,
                        "name": "queue",
                        "size": 2
                    },
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568365": {
                        "displayName": ...
                    },
                },
                "recommendations": {
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382": {
                        "displayName": "Passend zu Family Guy",
                        "id": "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382",
                        "index": 18,
                        "name": "similars",
                        "size": 33
                    },
                    "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568397": {
                        "displayName": ...
                    }
                }
            }
        """
        # prepare the return dictionary
        video_list_ids = {}
        for key in self.video_list_keys:
            video_list_ids[key] = {}

        # subcatogorize the lists by their context
        video_lists = response_data['lists']
        for video_list_id in video_lists.keys():
            video_list = video_lists[video_list_id]
            if video_list['context'] == 'genre':
                video_list_ids['genres'].update(self.parse_video_list_ids_entry(id=video_list_id, entry=video_list))
            elif video_list['context'] == 'similars' or video_list['context'] == 'becauseYouAdded':
                video_list_ids['recommendations'].update(self.parse_video_list_ids_entry(id=video_list_id, entry=video_list))
            else:
                video_list_ids['user'].update(self.parse_video_list_ids_entry(id=video_list_id, entry=video_list))

        return video_list_ids

    def parse_video_list_ids_entry (self, id, entry):
        """Parse a video id entry e.g. rip out the parts we need

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Dictionary entry from the ´fetch_video_list_ids´ call

        Returns
        -------
        id : :obj:`str`
            Unique id of the video list

        entry : :obj:`dict` of :obj:`str`
            Video list entry in the format:

            "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382": {
                "displayName": "Passend zu Family Guy",
                "id": "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382",
                "index": 18,
                "name": "similars",
                "size": 33
            }
        """
        return {
            id: {
                'id': id,
                'index': entry['index'],
                'name': entry['context'],
                'displayName': entry['displayName'],
                'size': entry['length']
            }
        }

    def parse_search_results (self, response_data):
        """Parse the list of search results, rip out the parts we need
           and extend it with detailed show informations

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the `fetch_search_results` call

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Search results in the format:

            {
                "70136140": {
                    "boxarts": "https://art-s.nflximg.net/0d7af/d5c72668c35d3da65ae031302bd4ae1bcc80d7af.jpg",
                    "detail_text": "Die legend\u00e4re und mit 13 Emmys nominierte Serie von Gene Roddenberry inspirierte eine ganze Generation.",
                    "id": "70136140",
                    "season_id": "70109435",
                    "synopsis": "Unter Befehl von Captain Kirk begibt sich die Besatzung des Raumschiffs Enterprise in die Tiefen des Weltraums, wo sie fremde Galaxien und neue Zivilisationen erforscht.",
                    "title": "Star Trek",
                    "type": "show"
                },
                "70158329": {
                    "boxarts": ...
                }
            }
        """
        search_results = {}
        raw_search_results = response_data['value']['videos']
        for entry_id in raw_search_results:
            if self._is_size_key(key=entry_id) == False:
                # fetch information about each show & build up a proper search results dictionary
                show = self.parse_show_list_entry(id=entry_id, entry=raw_search_results[entry_id])
                show[entry_id].update(self.parse_show_information(id=entry_id, response_data=self.fetch_show_information(id=entry_id, type=show[entry_id]['type'])))
                search_results.update(show)
        return search_results

    def parse_show_list_entry (self, id, entry):
        """Parse a show entry e.g. rip out the parts we need

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Dictionary entry from the ´fetch_show_information´ call

        id : :obj:`str`
            Unique id of the video list

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
            Show list entry in the format:

            {
                "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382": {
                    "id": "3589e2c6-ca3b-48b4-a72d-34f2c09ffbf4_11568382",
                    "title": "Enterprise",
                    "boxarts": "https://art-s.nflximg.net/.../smth.jpg",
                    "type": "show"
                }
            }
        """
        return {
            id: {
                'id': id,
                'title': entry['title'],
                'boxarts': entry['boxarts']['_342x192']['jpg']['url'],
                'type': entry['summary']['type']
            }
        }

    def parse_video_list (self, response_data):
        """Parse a list of videos

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the `fetch_video_list` call

        Returns
        -------
        :obj:`dict` of :obj:`dict`
            Video list in the format:

            {
                "372203": {
                    "artwork": null,
                    "boxarts": {
                      "big": "https://art-s.nflximg.net/5e7d3/b3b48749843fd3a36db11c319ffa60f96b55e7d3.jpg",
                      "small": "https://art-s.nflximg.net/57543/a039845c2eb9186dc26019576d895bf5a1957543.jpg"
                    },
                    "cast": [
                      "Christine Elise",
                      "Brad Dourif",
                      "Grace Zabriskie",
                      "Jenny Agutter",
                      "John Lafia",
                      "Gerrit Graham",
                      "Peter Haskell",
                      "Alex Vincent",
                      "Beth Grant"
                    ],
                    "creators": [],
                    "directors": [],
                    "episode_count": null,
                    "genres": [
                      "Horrorfilme"
                    ],
                    "id": "372203",
                    "in_my_list": true,
                    "interesting_moment": "https://art-s.nflximg.net/09544/ed4b3073394b4469fb6ec22b9df81a4f5cb09544.jpg",
                    "list_id": "9588df32-f957-40e4-9055-1f6f33b60103_46891306",
                    "maturity": {
                      "board": "FSK",
                      "description": "Nur f\u00fcr Erwachsene geeignet.",
                      "level": 1000,
                      "value": "18"
                    },
                    "quality": "540",
                    "rating": 3.1707757,
                    "regular_synopsis": "Ein Spielzeughersteller erweckt aus Versehen die Seele der M\u00f6rderpuppe Chucky erneut zum Leben, die sich unmittelbar wieder ihren m\u00f6rderischen Aktivit\u00e4ten zuwendet.",
                    "runtime": 5028,
                    "seasons_count": null,
                    "seasons_label": null,
                    "synopsis": "Die allseits beliebte, von D\u00e4monen besessene M\u00f6rderpuppe ist wieder da und verbreitet erneut Horror und Schrecken.",
                    "tags": [
                      "Brutal",
                      "Spannend"
                    ],
                    "title": "Chucky 2 \u2013 Die M\u00f6rderpuppe ist wieder da",
                    "type": "movie",
                    "watched": false,
                    "year": 1990
                },
                "80011356": {
                    "artwork": null,
                    "boxarts": {
                      "big": "https://art-s.nflximg.net/7c10d/5dcc3fc8f08487e92507627068cfe26ef727c10d.jpg",
                      "small": "https://art-s.nflximg.net/5bc0e/f3be361b8c594929062f90a8d9c6eb57fb75bc0e.jpg"
                    },
                    "cast": [
                      "Bjarne M\u00e4del"
                    ],
                    "creators": [],
                    "directors": [
                      "Arne Feldhusen"
                    ],
                    "episode_count": 24,
                    "genres": [
                      "Deutsche Serien",
                      "Serien",
                      "Comedyserien"
                    ],
                    "id": "80011356",
                    "in_my_list": true,
                    "interesting_moment": "https://art-s.nflximg.net/0188e/19cd705a71ee08c8d2609ae01cd8a97a86c0188e.jpg",
                    "list_id": "9588df32-f957-40e4-9055-1f6f33b60103_46891306",
                    "maturity": {
                      "board": "FSF",
                      "description": "Geeignet ab 12 Jahren.",
                      "level": 80,
                      "value": "12"
                    },
                    "quality": "720",
                    "rating": 4.4394655,
                    "regular_synopsis": "Comedy-Serie \u00fcber die Erlebnisse eines Tatortreinigers, der seine schmutzige Arbeit erst beginnen kann, wenn die Polizei die Tatortanalyse abgeschlossen hat.",
                    "runtime": null,
                    "seasons_count": 5,
                    "seasons_label": "5 Staffeln",
                    "synopsis": "In den meisten Krimiserien werden Mordf\u00e4lle auf faszinierende und spannende Weise gel\u00f6st. Diese Serie ist anders.",
                    "tags": [
                      "Zynisch"
                    ],
                    "title": "Der Tatortreiniger",
                    "type": "show",
                    "watched": false,
                    "year": 2015
                },
            }
        """
        video_list = {};
        raw_video_list = response_data['value']
        netflix_list_id = self.parse_netflix_list_id(video_list=raw_video_list);
        for video_id in raw_video_list['videos']:
            if self._is_size_key(key=video_id) == False:
                video_list.update(self.parse_video_list_entry(id=video_id, list_id=netflix_list_id, video=raw_video_list['videos'][video_id], persons=raw_video_list['person'], genres=raw_video_list['genres']))
        return video_list

    def parse_video_list_entry (self, id, list_id, video, persons, genres):
        """Parse a video list entry e.g. rip out the parts we need

        Parameters
        ----------
        id : :obj:`str`
            Unique id of the video

        list_id : :obj:`str`
            Unique id of the containing list

        video : :obj:`dict` of :obj:`str`
            Video entry from the ´fetch_video_list´ call

        persons : :obj:`dict` of :obj:`dict` of :obj:`str`
            List of persons with reference ids

        persons : :obj:`dict` of :obj:`dict` of :obj:`str`
            List of genres with reference ids

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
            Video list entry in the format:

           {
              "372203": {
                "artwork": null,
                "boxarts": {
                  "big": "https://art-s.nflximg.net/5e7d3/b3b48749843fd3a36db11c319ffa60f96b55e7d3.jpg",
                  "small": "https://art-s.nflximg.net/57543/a039845c2eb9186dc26019576d895bf5a1957543.jpg"
                },
                "cast": [
                  "Christine Elise",
                  "Brad Dourif",
                  "Grace Zabriskie",
                  "Jenny Agutter",
                  "John Lafia",
                  "Gerrit Graham",
                  "Peter Haskell",
                  "Alex Vincent",
                  "Beth Grant"
                ],
                "creators": [],
                "directors": [],
                "episode_count": null,
                "genres": [
                  "Horrorfilme"
                ],
                "id": "372203",
                "in_my_list": true,
                "interesting_moment": "https://art-s.nflximg.net/09544/ed4b3073394b4469fb6ec22b9df81a4f5cb09544.jpg",
                "list_id": "9588df32-f957-40e4-9055-1f6f33b60103_46891306",
                "maturity": {
                  "board": "FSK",
                  "description": "Nur f\u00fcr Erwachsene geeignet.",
                  "level": 1000,
                  "value": "18"
                },
                "quality": "540",
                "rating": 3.1707757,
                "regular_synopsis": "Ein Spielzeughersteller erweckt aus Versehen die Seele der M\u00f6rderpuppe Chucky erneut zum Leben, die sich unmittelbar wieder ihren m\u00f6rderischen Aktivit\u00e4ten zuwendet.",
                "runtime": 5028,
                "seasons_count": null,
                "seasons_label": null,
                "synopsis": "Die allseits beliebte, von D\u00e4monen besessene M\u00f6rderpuppe ist wieder da und verbreitet erneut Horror und Schrecken.",
                "tags": [
                  "Brutal",
                  "Spannend"
                ],
                "title": "Chucky 2 \u2013 Die M\u00f6rderpuppe ist wieder da",
                "type": "movie",
                "watched": false,
                "year": 1990
              }
            }
        """
        season_info = self.parse_season_information_for_video(video=video)
        return {
            id: {
                'id': id,
                'list_id': list_id,
                'title': video['title'],
                'synopsis': video['synopsis'],
                'regular_synopsis': video['regularSynopsis'],
                'type': video['summary']['type'],
                'rating': video['userRating']['average'],
                'episode_count': season_info['episode_count'],
                'seasons_label': season_info['seasons_label'],
                'seasons_count': season_info['seasons_count'],
                'in_my_list': video['queue']['inQueue'],
                'year': video['releaseYear'],
                'runtime': self.parse_runtime_for_video(video=video),
                'watched': video['watched'],
                'tags': self.parse_tags_for_video(video=video),
                'genres': self.parse_genres_for_video(video=video, genres=genres),
                'quality': self.parse_quality_for_video(video=video),
                'cast': self.parse_cast_for_video(video=video, persons=persons),
                'directors': self.parse_directors_for_video(video=video, persons=persons),
                'creators': self.parse_creators_for_video(video=video, persons=persons),
                'maturity': {
                    'board': None if 'board' not in video['maturity']['rating'].keys() else video['maturity']['rating']['board'],
                    'value': None if 'value' not in video['maturity']['rating'].keys() else video['maturity']['rating']['value'],
                    'description': None if 'maturityDescription' not in video['maturity']['rating'].keys() else video['maturity']['rating']['maturityDescription'],
                    'level': None if 'maturityLevel' not in video['maturity']['rating'].keys() else video['maturity']['rating']['maturityLevel']
                },
                'boxarts': {
                    'small': video['boxarts']['_342x192']['jpg']['url'],
                    'big': video['boxarts']['_1280x720']['jpg']['url']
                },
                'interesting_moment': None if 'interestingMoment' not in video.keys() else video['interestingMoment']['_665x375']['jpg']['url'],
                'artwork': video['artWorkByType']['BILLBOARD']['_1280x720']['jpg']['url'],
            }
        }

    def parse_creators_for_video (self, video, persons):
        """Matches ids with person names to generate a list of creators

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        persons : :obj:`dict` of :obj:`str`
            Raw resposne of all persons delivered by the API call

        Returns
        -------
        :obj:`list` of :obj:`str`
            List of creators
        """
        creators = []
        for person_key in dict(persons).keys():
            if self._is_size_key(key=person_key) == False and person_key != 'summary':
                for creator_key in dict(video['creators']).keys():
                    if self._is_size_key(key=creator_key) == False and creator_key != 'summary':
                        if video['creators'][creator_key][1] == person_key:
                            creators.append(persons[person_key]['name'])
        return creators

    def parse_directors_for_video (self, video, persons):
        """Matches ids with person names to generate a list of directors

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        persons : :obj:`dict` of :obj:`str`
            Raw resposne of all persons delivered by the API call

        Returns
        -------
        :obj:`list` of :obj:`str`
            List of directors
        """
        directors = []
        for person_key in dict(persons).keys():
            if self._is_size_key(key=person_key) == False and person_key != 'summary':
                for director_key in dict(video['directors']).keys():
                    if self._is_size_key(key=director_key) == False and director_key != 'summary':
                        if video['directors'][director_key][1] == person_key:
                            directors.append(persons[person_key]['name'])
        return directors

    def parse_cast_for_video (self, video, persons):
        """Matches ids with person names to generate a list of cast members

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        persons : :obj:`dict` of :obj:`str`
            Raw resposne of all persons delivered by the API call

        Returns
        -------
        :obj:`list` of :obj:`str`
            List of cast members
        """
        cast = []
        for person_key in dict(persons).keys():
            if self._is_size_key(key=person_key) == False and person_key != 'summary':
                for cast_key in dict(video['cast']).keys():
                    if self._is_size_key(key=cast_key) == False and cast_key != 'summary':
                        if video['cast'][cast_key][1] == person_key:
                            cast.append(persons[person_key]['name'])
        return cast

    def parse_genres_for_video (self, video, genres):
        """Matches ids with genre names to generate a list of genres for a video

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        genres : :obj:`dict` of :obj:`str`
            Raw resposne of all genres delivered by the API call

        Returns
        -------
        :obj:`list` of :obj:`str`
            List of genres
        """
        video_genres = []
        for genre_key in dict(genres).keys():
            if self._is_size_key(key=genre_key) == False and genre_key != 'summary':
                for show_genre_key in dict(video['genres']).keys():
                    if self._is_size_key(key=show_genre_key) == False and show_genre_key != 'summary':
                        if video['genres'][show_genre_key][1] == genre_key:
                            video_genres.append(genres[genre_key]['name'])
        return video_genres

    def parse_tags_for_video (self, video):
        """Parses a nested list of tags, removes the not needed meta information & returns a raw string list

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        Returns
        -------
        :obj:`list` of :obj:`str`
            List of tags
        """
        tags = []
        for tag_key in dict(video['tags']).keys():
            if self._is_size_key(key=tag_key) == False and tag_key != 'summary':
                tags.append(video['tags'][tag_key]['name'])
        return tags

    def parse_season_information_for_video (self, video):
        """Checks if the fiven video is a show (series) and returns season & episode information

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        Returns
        -------
        :obj:`dict` of :obj:`str`
            Episode count / Season Count & Season label if given
        """
        season_info = {
            'episode_count': None,
            'seasons_label': None,
            'seasons_count': None
        }
        if video['summary']['type'] == 'show':
            season_info = {
                'episode_count': video['episodeCount'],
                'seasons_label': video['numSeasonsLabel'],
                'seasons_count': video['seasonCount']
            }
        return season_info

    def parse_quality_for_video (self, video):
        """Transforms Netflix quality information in video resolution info

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        Returns
        -------
        :obj:`str`
            Quality of the video
        """
        quality = '540'
        if video['videoQuality']['hasHD']:
            quality = '720'
        if video['videoQuality']['hasUltraHD']:
            quality = '1080'
        return quality

    def parse_runtime_for_video (self, video):
        """Checks if the video is a movie & returns the runtime if given

        Parameters
        ----------
        video : :obj:`dict` of :obj:`str`
            Dictionary entry for one video entry

        Returns
        -------
        :obj:`str`
            Runtime of the video (in seconds)
        """
        runtime = None
        if video['summary']['type'] != 'show':
            runtime = video['runtime']
        return runtime

    def parse_netflix_list_id (self, video_list):
        """Parse a video list and extract the list id

        Parameters
        ----------
        video_list : :obj:`dict` of :obj:`str`
            Netflix video list

        Returns
        -------
        entry : :obj:`str` or None
            Netflix list id
        """
        netflix_list_id = None
        if 'lists' in video_list.keys():
            for video_id in video_list['lists']:
                if self._is_size_key(key=video_id) == False:
                    netflix_list_id = video_id;
        return netflix_list_id

    def parse_show_information (self, id, response_data):
        """Parse extended show information (synopsis, seasons, etc.)

        Parameters
        ----------
        id : :obj:`str`
            Video id

        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the `fetch_show_information` call

        Returns
        -------
        entry : :obj:`dict` of :obj:`str`
        Show information in the format:
            {
                "season_id": "80113084",
                "synopsis": "Aus verzweifelter Geldnot versucht sich der Familienvater und Drucker Jochen als Geldf\u00e4lscher und rutscht dabei immer mehr in die dunkle Welt des Verbrechens ab."
                "detail_text": "I´m optional"
            }
        """
        show = {}
        raw_show = response_data['value']['videos'][id]
        show.update({'synopsis': raw_show['regularSynopsis']})
        if 'evidence' in raw_show:
            show.update({'detail_text': raw_show['evidence']['value']['text']})
        if 'seasonList' in raw_show:
            show.update({'season_id': raw_show['seasonList']['current'][1]})
        return show

    def parse_seasons (self, id, response_data):
        """Parse a list of seasons for a given show

        Parameters
        ----------
        id : :obj:`str`
            Season id

        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the `fetch_seasons_for_show` call

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
        Season information in the format:
            {
                "80113084": {
                    "id": 80113084,
                    "text": "Season 1",
                    "shortName": "St. 1",
                    "boxarts": {
                      "big": "https://art-s.nflximg.net/5e7d3/b3b48749843fd3a36db11c319ffa60f96b55e7d3.jpg",
                      "small": "https://art-s.nflximg.net/57543/a039845c2eb9186dc26019576d895bf5a1957543.jpg"
                    },
                    "interesting_moment": "https://art-s.nflximg.net/09544/ed4b3073394b4469fb6ec22b9df81a4f5cb09544.jpg"
                },
                "80113085": {
                    "id": 80113085,
                    "text": "Season 2",
                    "shortName": "St. 2",
                    "boxarts": {
                      "big": "https://art-s.nflximg.net/5e7d3/b3b48749843fd3a36db11c319ffa60f96b55e7d3.jpg",
                      "small": "https://art-s.nflximg.net/57543/a039845c2eb9186dc26019576d895bf5a1957543.jpg"
                    },
                    "interesting_moment": "https://art-s.nflximg.net/09544/ed4b3073394b4469fb6ec22b9df81a4f5cb09544.jpg"
                }
            }
        """
        seasons = {}
        raw_seasons = response_data['value']
        for season in raw_seasons['seasons']:
            if self._is_size_key(key=season) == False:
                seasons.update(self.parse_season_entry(season=raw_seasons['seasons'][season], videos=raw_seasons['videos']))
        return seasons

    def parse_season_entry (self, season, videos):
        """Parse a season list entry e.g. rip out the parts we need

        Parameters
        ----------
        season : :obj:`dict` of :obj:`str`
            Season entry from the `fetch_seasons_for_show` call

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
            Season list entry in the format:

            {
                "80113084": {
                    "id": 80113084,
                    "text": "Season 1",
                    "shortName": "St. 1",
                    "boxarts": {
                      "big": "https://art-s.nflximg.net/5e7d3/b3b48749843fd3a36db11c319ffa60f96b55e7d3.jpg",
                      "small": "https://art-s.nflximg.net/57543/a039845c2eb9186dc26019576d895bf5a1957543.jpg"
                    },
                    "interesting_moment": "https://art-s.nflximg.net/09544/ed4b3073394b4469fb6ec22b9df81a4f5cb09544.jpg"
                }
            }
        """
        # get art video key
        video_key = ''
        for key in videos.keys():
            if self._is_size_key(key=key) == False:
                video_key = key
        return {
            season['summary']['id']: {
                'id': season['summary']['id'],
                'text': season['summary']['name'],
                'shortName': season['summary']['shortName'],
                'boxarts': {
                    'small': videos[video_key]['boxarts']['_342x192']['jpg']['url'],
                    'big': videos[video_key]['boxarts']['_1280x720']['jpg']['url']
                },
                'interesting_moment': videos[video_key]['interestingMoment']['_665x375']['jpg']['url'],
            }
        }

    def parse_episodes_by_season (self, response_data):
        """Parse episodes for a given season/episode list

        Parameters
        ----------
        response_data : :obj:`dict` of :obj:`str`
            Parsed response JSON from the `fetch_seasons_for_show` call

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
        Season information in the format:

        {
          "70251729": {
            "banner": "https://art-s.nflximg.net/63a36/c7fdfe6604ef2c22d085ac5dca5f69874e363a36.jpg",
            "duration": 1387,
            "episode": 1,
            "fanart": "https://art-s.nflximg.net/74e02/e7edcc5cc7dcda1e94d505df2f0a2f0d22774e02.jpg",
            "genres": [
              "Serien",
              "Comedyserien"
            ],
            "id": 70251729,
            "mediatype": "episode",
            "mpaa": "FSK 16",
            "my_list": false,
            "playcount": 0,
            "plot": "Als die Griffins und andere Einwohner von Quahog in die Villa von James Woods eingeladen werden, muss pl\u00f6tzlich ein Mord aufgekl\u00e4rt werden.",
            "poster": "https://art-s.nflximg.net/72fd6/57088715e8d436fdb6986834ab39124b0a972fd6.jpg",
            "rating": 3.9111512,
            "season": 9,
            "thumb": "https://art-s.nflximg.net/be686/07680670a68da8749eba607efb1ae37f9e3be686.jpg",
            "title": "Und dann gab es weniger (Teil 1)",
            "year": 2010,
            "bookmark": -1
          },
          "70251730": {
            "banner": "https://art-s.nflximg.net/63a36/c7fdfe6604ef2c22d085ac5dca5f69874e363a36.jpg",
            "duration": 1379,
            "episode": 2,
            "fanart": "https://art-s.nflximg.net/c472c/6c10f9578bf2c1d0a183c2ccb382931efcbc472c.jpg",
            "genres": [
              "Serien",
              "Comedyserien"
            ],
            "id": 70251730,
            "mediatype": "episode",
            "mpaa": "FSK 16",
            "my_list": false,
            "playcount": 1,
            "plot": "Wer ist der M\u00f6rder? Nach zahlreichen Morden wird immer wieder jemand anderes verd\u00e4chtigt.",
            "poster": "https://art-s.nflximg.net/72fd6/57088715e8d436fdb6986834ab39124b0a972fd6.jpg",
            "rating": 3.9111512,
            "season": 9,
            "thumb": "https://art-s.nflximg.net/15a08/857d59126641987bec302bb147a802a00d015a08.jpg",
            "title": "Und dann gab es weniger (Teil 2)",
            "year": 2010,
            "bookmark": 1234
          },
        }
        """
        episodes = {}
        raw_episodes = response_data['value']['videos']
        for episode_id in raw_episodes:
            if self._is_size_key(key=episode_id) == False:
                if (raw_episodes[episode_id]['summary']['type'] == 'episode'):
                    episodes.update(self.parse_episode(episode=raw_episodes[episode_id], genres=response_data['value']['genres']))
        return episodes

    def parse_episode (self, episode, genres=None):
        """Parse episode from an list of episodes by season

        Parameters
        ----------
        episode : :obj:`dict` of :obj:`str`
            Episode entry from the `fetch_episodes_by_season` call

        Returns
        -------
        entry : :obj:`dict` of :obj:`dict` of :obj:`str`
        Episode information in the format:

        {
          "70251729": {
            "banner": "https://art-s.nflximg.net/63a36/c7fdfe6604ef2c22d085ac5dca5f69874e363a36.jpg",
            "duration": 1387,
            "episode": 1,
            "fanart": "https://art-s.nflximg.net/74e02/e7edcc5cc7dcda1e94d505df2f0a2f0d22774e02.jpg",
            "genres": [
              "Serien",
              "Comedyserien"
            ],
            "id": 70251729,
            "mediatype": "episode",
            "mpaa": "FSK 16",
            "my_list": false,
            "playcount": 0,
            "plot": "Als die Griffins und andere Einwohner von Quahog in die Villa von James Woods eingeladen werden, muss pl\u00f6tzlich ein Mord aufgekl\u00e4rt werden.",
            "poster": "https://art-s.nflximg.net/72fd6/57088715e8d436fdb6986834ab39124b0a972fd6.jpg",
            "rating": 3.9111512,
            "season": 9,
            "thumb": "https://art-s.nflximg.net/be686/07680670a68da8749eba607efb1ae37f9e3be686.jpg",
            "title": "Und dann gab es weniger (Teil 1)",
            "year": 2010,
            "bookmark": 1234
          },
        }
        """
        return {
            episode['summary']['id']: {
                'id': episode['summary']['id'],
                'episode': episode['summary']['episode'],
                'season': episode['summary']['season'],
                'plot': episode['info']['synopsis'],
                'duration': episode['info']['runtime'],
                'title': episode['info']['title'],
                'year': episode['info']['releaseYear'],
                'genres': self.parse_genres_for_video(video=episode, genres=genres),
                'mpaa': str(episode['maturity']['rating']['board']) + ' ' + str(episode['maturity']['rating']['value']),
                'maturity': episode['maturity'],
                'playcount': (0, 1)[episode['watched']],
                'rating': episode['userRating']['average'],
                'thumb': episode['info']['interestingMoments']['url'],
                'fanart': episode['interestingMoment']['_1280x720']['jpg']['url'],
                'poster': episode['boxarts']['_1280x720']['jpg']['url'],
                'banner': episode['boxarts']['_342x192']['jpg']['url'],
                'mediatype': {'episode': 'episode', 'movie': 'movie'}[episode['summary']['type']],
                'my_list': episode['queue']['inQueue'],
                'bookmark': episode['bookmarkPosition']
            }
        }

    def fetch_browse_list_contents (self):
        """Fetches the HTML data for the lists on the landing page (browse page) of Netflix

        Returns
        -------
        :obj:`BeautifulSoup`
            Instance of an BeautifulSoup document containing the complete page contents
        """
        response = self.session.get(self._get_document_url_for(component='browse'), verify=self.verify_ssl)
        return BeautifulSoup(response.text)

    def fetch_video_list_ids (self, list_from=0, list_to=50):
        """Fetches the JSON with detailed information based on the lists on the landing page (browse page) of Netflix

        Parameters
        ----------
        list_from : :obj:`int`
            Start entry for pagination

        list_to : :obj:`int`
            Last entry for pagination

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        payload = {
            'fromRow': list_from,
            'toRow': list_to,
            'opaqueImageExtension': 'jpg',
            'transparentImageExtension': 'png',
            '_': int(time.time()),
            'authURL': self.user_data['authURL']
        }
        url = self._get_api_url_for(component='video_list_ids')
        response = self.session.get(url, params=payload, verify=self.verify_ssl);
        return self._process_response(response=response, component=url)

    def fetch_search_results (self, search_str, list_from=0, list_to=10):
        """Fetches the JSON which contains the results for the given search query

        Parameters
        ----------
        search_str : :obj:`str`
            String to query Netflix search for

        list_from : :obj:`int`
            Start entry for pagination

        list_to : :obj:`int`
            Last entry for pagination

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        # properly encode the search string
        encoded_search_string = urllib.quote(search_str)

        paths = [
            ['search', encoded_search_string, 'titles', {'from': list_from, 'to': list_to}, ['summary', 'title']],
            ['search', encoded_search_string, 'titles', {'from': list_from, 'to': list_to}, 'boxarts', '_342x192', 'jpg'],
            ['search', encoded_search_string, 'titles', ['id', 'length', 'name', 'trackIds', 'requestId']],
            ['search', encoded_search_string, 'suggestions', 0, 'relatedvideos', {'from': list_from, 'to': list_to}, ['summary', 'title']],
            ['search', encoded_search_string, 'suggestions', 0, 'relatedvideos', {'from': list_from, 'to': list_to}, 'boxarts', '_342x192', 'jpg'],
            ['search', encoded_search_string, 'suggestions', 0, 'relatedvideos', ['id', 'length', 'name', 'trackIds', 'requestId']]
        ]
        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='Search results')

    def fetch_video_list (self, list_id, list_from=0, list_to=20):
        """Fetches the JSON which contains the contents of a given video list

        Parameters
        ----------
        list_id : :obj:`str`
            Unique list id to query Netflix for

        list_from : :obj:`int`
            Start entry for pagination

        list_to : :obj:`int`
            Last entry for pagination

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        paths = [
            ['lists', list_id, {'from': list_from, 'to': list_to}, ['summary', 'title', 'synopsis', 'regularSynopsis', 'evidence', 'queue', 'episodeCount', 'info', 'maturity', 'runtime', 'seasonCount', 'releaseYear', 'userRating', 'numSeasonsLabel', 'bookmarkPosition', 'watched', 'videoQuality']],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'cast', {'from': 0, 'to': 15}, ['id', 'name']],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'cast', 'summary'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'genres', {'from': 0, 'to': 5}, ['id', 'name']],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'genres', 'summary'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'tags', {'from': 0, 'to': 9}, ['id', 'name']],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'tags', 'summary'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, ['creators', 'directors'], {'from': 0, 'to': 49}, ['id', 'name']],
            ['lists', list_id, {'from': list_from, 'to': list_to}, ['creators', 'directors'], 'summary'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'bb2OGLogo', '_400x90', 'png'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'boxarts', '_342x192', 'jpg'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'boxarts', '_1280x720', 'jpg'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'storyarts', '_1632x873', 'jpg'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'interestingMoment', '_665x375', 'jpg'],
            ['lists', list_id, {'from': list_from, 'to': list_to}, 'artWorkByType', 'BILLBOARD', '_1280x720', 'jpg']
        ];

        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='Video list')

    def fetch_video_list_information (self, video_ids):
        """Fetches the JSON which contains the detail information of a list of given video ids

        Parameters
        ----------
        video_ids : :obj:`list` of :obj:`str`
            List of video ids to fetch detail data for

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        paths = []
        for video_id in video_ids:
            paths.append(['videos', video_id, ['summary', 'title', 'synopsis', 'regularSynopsis', 'evidence', 'queue', 'episodeCount', 'info', 'maturity', 'runtime', 'seasonCount', 'releaseYear', 'userRating', 'numSeasonsLabel', 'bookmarkPosition', 'watched', 'videoQuality']])
            paths.append(['videos', video_id, 'cast', {'from': 0, 'to': 15}, ['id', 'name']])
            paths.append(['videos', video_id, 'cast', 'summary'])
            paths.append(['videos', video_id, 'genres', {'from': 0, 'to': 5}, ['id', 'name']])
            paths.append(['videos', video_id, 'genres', 'summary'])
            paths.append(['videos', video_id, 'tags', {'from': 0, 'to': 9}, ['id', 'name']])
            paths.append(['videos', video_id, 'tags', 'summary'])
            paths.append(['videos', video_id, ['creators', 'directors'], {'from': 0, 'to': 49}, ['id', 'name']])
            paths.append(['videos', video_id, ['creators', 'directors'], 'summary'])
            paths.append(['videos', video_id, 'bb2OGLogo', '_400x90', 'png'])
            paths.append(['videos', video_id, 'boxarts', '_342x192', 'jpg'])
            paths.append(['videos', video_id, 'boxarts', '_1280x720', 'jpg'])
            paths.append(['videos', video_id, 'storyarts', '_1632x873', 'jpg'])
            paths.append(['videos', video_id, 'interestingMoment', '_665x375', 'jpg'])
            paths.append(['videos', video_id, 'artWorkByType', 'BILLBOARD', '_1280x720', 'jpg'])

        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='fetch_video_list_information')

    def fetch_metadata (self, id):
        """Fetches the JSON which contains the metadata for a given show/movie or season id

        Parameters
        ----------
        id : :obj:`str`
            Show id, movie id or season id

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        payload = {
            'movieid': id,
            'imageformat': 'jpg',
            '_': int(time.time())
        }
        url = self._get_api_url_for(component='metadata')
        response = self.session.get(url, params=payload, verify=self.verify_ssl);
        return self._process_response(response=response, component=url)

    def fetch_show_information (self, id, type):
        """Fetches the JSON which contains the detailed contents of a show

        Parameters
        ----------
        id : :obj:`str`
            Unique show id to query Netflix for

        type : :obj:`str`
            Can be 'movie' or 'show'

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        # check if we have a show or a movie, the request made depends on this
        if type == 'show':
            paths = [
                ['videos', id, ['requestId', 'regularSynopsis', 'evidence']],
                ['videos', id, 'seasonList', 'current', 'summary']
            ]
        else:
            paths = [['videos', id, ['requestId', 'regularSynopsis', 'evidence']]]
        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='Show information')

    def fetch_seasons_for_show (self, id, list_from=0, list_to=30):
        """Fetches the JSON which contains the seasons of a given show

        Parameters
        ----------
        id : :obj:`str`
            Unique show id to query Netflix for

        list_from : :obj:`int`
            Start entry for pagination

        list_to : :obj:`int`
            Last entry for pagination

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        paths = [
            ['videos', id, 'seasonList', {'from': list_from, 'to': list_to}, 'summary'],
            ['videos', id, 'seasonList', 'summary'],
            ['videos', id, 'boxarts',  '_342x192', 'jpg'],
            ['videos', id, 'boxarts', '_1280x720', 'jpg'],
            ['videos', id, 'storyarts',  '_1632x873', 'jpg'],
            ['videos', id, 'interestingMoment', '_665x375', 'jpg']
        ]
        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='Seasons')

    def fetch_episodes_by_season (self, season_id, list_from=-1, list_to=40):
        """Fetches the JSON which contains the episodes of a given season

        TODO: Add more metadata

        Parameters
        ----------
        season_id : :obj:`str`
            Unique season_id id to query Netflix for

        list_from : :obj:`int`
            Start entry for pagination

        list_to : :obj:`int`
            Last entry for pagination

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        paths = [
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, ['summary', 'queue', 'info', 'maturity', 'userRating', 'bookmarkPosition', 'creditOffset', 'watched', 'videoQuality']],
            #['videos', season_id, 'cast', {'from': 0, 'to': 15}, ['id', 'name']],
            #['videos', season_id, 'cast', 'summary'],
            #['videos', season_id, 'genres', {'from': 0, 'to': 5}, ['id', 'name']],
            #['videos', season_id, 'genres', 'summary'],
            #['videos', season_id, 'tags', {'from': 0, 'to': 9}, ['id', 'name']],
            #['videos', season_id, 'tags', 'summary'],
            #['videos', season_id, ['creators', 'directors'], {'from': 0, 'to': 49}, ['id', 'name']],
            #['videos', season_id, ['creators', 'directors'], 'summary'],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'genres', {'from': 0, 'to': 1}, ['id', 'name']],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'genres', 'summary'],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'interestingMoment', '_1280x720', 'jpg'],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'interestingMoment', '_665x375', 'jpg'],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'boxarts', '_342x192', 'jpg'],
            ['seasons', season_id, 'episodes', {'from': list_from, 'to': list_to}, 'boxarts', '_1280x720', 'jpg']
        ]
        response = self._path_request(paths=paths)
        return self._process_response(response=response, component='fetch_episodes_by_season')

    def refresh_session_data (self, account):
        """Reload the session data (profiles, user_data, api_data)

        Parameters
        ----------
        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property
        """
        # load the profiles page (to verify the user)
        response = self.session.get(self._get_document_url_for(component='profiles'), verify=self.verify_ssl)
        # parse out the needed inline information
        page_soup = BeautifulSoup(response.text)
        page_data = self.extract_inline_netflix_page_data(page_soup=page_soup)
        self._parse_page_contents(page_soup)
        account_hash = self._generate_account_hash(account=account)
        self._save_data(filename=self.data_path + '_' + account_hash)

    def _path_request (self, paths):
        """Executes a post request against the shakti endpoint with Falcor style payload

        Parameters
        ----------
        paths : :obj:`list` of :obj:`list`
            Payload with path querys for the Netflix Shakti API in Falcor style

        Returns
        -------
        :obj:`requests.response`
            Response from a POST call made with Requests
        """
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/javascript, */*',
        }

        data = json.dumps({
            'paths': paths,
            'authURL': self.user_data['authURL']
        })

        params = {
            'withSize': True,
            'materialize': True,
            'model': self.user_data['gpsModel']
        }

        return self.session.post(self._get_api_url_for(component='shakti'), params=params, headers=headers, data=data, verify=self.verify_ssl)

    def _is_size_key (self, key):
        """Tiny helper that checks if a given key is called $size or size, as we need to check this often

        Parameters
        ----------
        key : :obj:`str`
            Key to check the value for

        Returns
        -------
        bool
            Key has a size value or not
        """
        return key == '$size' or key == 'size'

    def _get_api_url_for (self, component):
        """Tiny helper that builds the url for a requested API endpoint component

        Parameters
        ----------
        component : :obj:`str`
            Component endpoint to build the URL for

        Returns
        -------
        :obj:`str`
            API Url
        """
        return self.api_data['API_ROOT'] + self.api_data['API_BASE_URL'] + '/' + self.api_data['BUILD_IDENTIFIER'] + self.urls[component]

    def _get_document_url_for (self, component):
        """Tiny helper that builds the url for a requested document endpoint component

        Parameters
        ----------
        component : :obj:`str`
            Component endpoint to build the URL for

        Returns
        -------
        :obj:`str`
            Document Url
        """
        return self.base_url + self.urls[component]

    def _process_response (self, response, component):
        """Tiny helper to check responses for API requests

        Parameters
        ----------
        response : :obj:`requests.response`
            Response from a requests instance

        component : :obj:`str`
            Component endpoint

        Returns
        -------
        :obj:`dict` of :obj:`dict` of :obj:`str` or :obj:`dict` of :obj:`str`
            Raw Netflix API call response or api call error
        """
        # check if we´re not authorized to make thios call
        if response.status_code == 401:
            return {
                'error': True,
                'message': 'Session invalid',
                'code': 401
            }
        # check if somethign else failed
        if response.status_code != 200:
            return {
                'error': True,
                'message': 'API call for "' + component + '" failed',
                'code': response.status_code
            }
        # return the parsed response & everything´s fine
        return response.json()

    def _update_my_list (self, video_id, operation):
        """Tiny helper to add & remove items from "my list"

        Parameters
        ----------
        video_id : :obj:`str`
            ID of the show/movie to be added

        operation : :obj:`str`
            Either "add" or "remove"

        Returns
        -------
        bool
            Operation successfull
        """
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/javascript, */*',
        }

        payload = json.dumps({
            'operation': operation,
            'videoId': int(video_id),
            'authURL': self.user_data['authURL']
        })

        response = self.session.post(self._get_api_url_for(component='update_my_list'), headers=headers, data=payload, verify=self.verify_ssl)
        return response.status_code == 200

    def _save_data(self, filename):
        """Tiny helper that stores session data from the session in a given file

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to store the cookie

        Returns
        -------
        bool
            Storage procedure was successfull
        """
        if not os.path.isdir(os.path.dirname(filename)):
            return False
        with open(filename, 'w') as f:
            f.truncate()
            pickle.dump({
                'user_data': self.user_data,
                'api_data': self.api_data,
                'profiles': self.profiles
            }, f)

    def _load_data(self, filename):
        """Tiny helper that loads session data into the active session from a given file

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to load the data from

        Returns
        -------
        bool
            Load procedure was successfull
        """
        if not os.path.isfile(filename):
            return False

        with open(filename) as f:
            data = pickle.load(f)
            if data:
                self.profiles = data['profiles']
                self.user_data = data['user_data']
                self.api_data = data['api_data']
            else:
                return False

    def _delete_data (self, path):
        """Tiny helper that deletes session data

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to delete the files

        """
        head, tail = os.path.split(path)
        for subdir, dirs, files in os.walk(head):
            for file in files:
                if tail in file:
                    os.remove(os.path.join(subdir, file))

    def _save_cookies(self, filename):
        """Tiny helper that stores cookies from the session in a given file

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to store the cookie

        Returns
        -------
        bool
            Storage procedure was successfull
        """
        if not os.path.isdir(os.path.dirname(filename)):
            return False
        with open(filename, 'w') as f:
            f.truncate()
            pickle.dump(self.session.cookies._cookies, f)

    def _load_cookies(self, filename):
        """Tiny helper that loads cookies into the active session from a given file

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to load the cookie from

        Returns
        -------
        bool
            Load procedure was successfull
        """
        if not os.path.isfile(filename):
            return False

        with open(filename) as f:
            cookies = pickle.load(f)
            if cookies:
                jar = requests.cookies.RequestsCookieJar()
                jar._cookies = cookies
                self.session.cookies = jar
            else:
                return False

    def _delete_cookies (self, path):
        """Tiny helper that deletes cookie data

        Parameters
        ----------
        filename : :obj:`str`
            Complete path incl. filename that determines where to delete the files

        """
        head, tail = os.path.split(path)
        for subdir, dirs, files in os.walk(head):
            for file in files:
                if tail in file:
                    os.remove(os.path.join(subdir, file))

    def _generate_account_hash (self, account):
        """Generates a has for the given account (used for cookie verification)

        Parameters
        ----------
        account : :obj:`dict` of :obj:`str`
            Dict containing an email, country & a password property

        Returns
        -------
        :obj:`str`
            Account data hash
        """
        return base64.urlsafe_b64encode(account['email'])