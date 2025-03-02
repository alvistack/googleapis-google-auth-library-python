# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import http.client
import json
import os
import urllib

import mock
import pytest

from google.auth import _helpers
from google.auth import exceptions
from google.auth import identity_pool
from google.auth import transport


CLIENT_ID = "username"
CLIENT_SECRET = "password"
# Base64 encoding of "username:password".
BASIC_AUTH_ENCODING = "dXNlcm5hbWU6cGFzc3dvcmQ="
SERVICE_ACCOUNT_EMAIL = "service-1234@service-name.iam.gserviceaccount.com"
SERVICE_ACCOUNT_IMPERSONATION_URL = (
    "https://us-east1-iamcredentials.googleapis.com/v1/projects/-"
    + "/serviceAccounts/{}:generateAccessToken".format(SERVICE_ACCOUNT_EMAIL)
)
QUOTA_PROJECT_ID = "QUOTA_PROJECT_ID"
SCOPES = ["scope1", "scope2"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SUBJECT_TOKEN_TEXT_FILE = os.path.join(DATA_DIR, "external_subject_token.txt")
SUBJECT_TOKEN_JSON_FILE = os.path.join(DATA_DIR, "external_subject_token.json")
SUBJECT_TOKEN_FIELD_NAME = "access_token"

with open(SUBJECT_TOKEN_TEXT_FILE) as fh:
    TEXT_FILE_SUBJECT_TOKEN = fh.read()

with open(SUBJECT_TOKEN_JSON_FILE) as fh:
    JSON_FILE_CONTENT = json.load(fh)
    JSON_FILE_SUBJECT_TOKEN = JSON_FILE_CONTENT.get(SUBJECT_TOKEN_FIELD_NAME)

TOKEN_URL = "https://sts.googleapis.com/v1/token"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
AUDIENCE = "//iam.googleapis.com/projects/123456/locations/global/workloadIdentityPools/POOL_ID/providers/PROVIDER_ID"


class TestCredentials(object):
    CREDENTIAL_SOURCE_TEXT = {"file": SUBJECT_TOKEN_TEXT_FILE}
    CREDENTIAL_SOURCE_JSON = {
        "file": SUBJECT_TOKEN_JSON_FILE,
        "format": {"type": "json", "subject_token_field_name": "access_token"},
    }
    CREDENTIAL_URL = "http://fakeurl.com"
    CREDENTIAL_SOURCE_TEXT_URL = {"url": CREDENTIAL_URL}
    CREDENTIAL_SOURCE_JSON_URL = {
        "url": CREDENTIAL_URL,
        "format": {"type": "json", "subject_token_field_name": "access_token"},
    }
    SUCCESS_RESPONSE = {
        "access_token": "ACCESS_TOKEN",
        "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": " ".join(SCOPES),
    }

    @classmethod
    def make_mock_response(cls, status, data):
        response = mock.create_autospec(transport.Response, instance=True)
        response.status = status
        if isinstance(data, dict):
            response.data = json.dumps(data).encode("utf-8")
        else:
            response.data = data
        return response

    @classmethod
    def make_mock_request(
        cls, token_status=http.client.OK, token_data=None, *extra_requests
    ):
        responses = []
        responses.append(cls.make_mock_response(token_status, token_data))

        while len(extra_requests) > 0:
            # If service account impersonation is requested, mock the expected response.
            status, data, extra_requests = (
                extra_requests[0],
                extra_requests[1],
                extra_requests[2:],
            )
            responses.append(cls.make_mock_response(status, data))

        request = mock.create_autospec(transport.Request)
        request.side_effect = responses

        return request

    @classmethod
    def assert_credential_request_kwargs(
        cls, request_kwargs, headers, url=CREDENTIAL_URL
    ):
        assert request_kwargs["url"] == url
        assert request_kwargs["method"] == "GET"
        assert request_kwargs["headers"] == headers
        assert request_kwargs.get("body", None) is None

    @classmethod
    def assert_token_request_kwargs(
        cls, request_kwargs, headers, request_data, token_url=TOKEN_URL
    ):
        assert request_kwargs["url"] == token_url
        assert request_kwargs["method"] == "POST"
        assert request_kwargs["headers"] == headers
        assert request_kwargs["body"] is not None
        body_tuples = urllib.parse.parse_qsl(request_kwargs["body"])
        assert len(body_tuples) == len(request_data.keys())
        for (k, v) in body_tuples:
            assert v.decode("utf-8") == request_data[k.decode("utf-8")]

    @classmethod
    def assert_impersonation_request_kwargs(
        cls,
        request_kwargs,
        headers,
        request_data,
        service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
    ):
        assert request_kwargs["url"] == service_account_impersonation_url
        assert request_kwargs["method"] == "POST"
        assert request_kwargs["headers"] == headers
        assert request_kwargs["body"] is not None
        body_json = json.loads(request_kwargs["body"].decode("utf-8"))
        assert body_json == request_data

    @classmethod
    def assert_underlying_credentials_refresh(
        cls,
        credentials,
        audience,
        subject_token,
        subject_token_type,
        token_url,
        service_account_impersonation_url=None,
        basic_auth_encoding=None,
        quota_project_id=None,
        used_scopes=None,
        credential_data=None,
        scopes=None,
        default_scopes=None,
    ):
        """Utility to assert that a credentials are initialized with the expected
        attributes by calling refresh functionality and confirming response matches
        expected one and that the underlying requests were populated with the
        expected parameters.
        """
        # STS token exchange request/response.
        token_response = cls.SUCCESS_RESPONSE.copy()
        token_headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if basic_auth_encoding:
            token_headers["Authorization"] = "Basic " + basic_auth_encoding

        if service_account_impersonation_url:
            token_scopes = "https://www.googleapis.com/auth/iam"
        else:
            token_scopes = " ".join(used_scopes or [])

        token_request_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "audience": audience,
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": token_scopes,
            "subject_token": subject_token,
            "subject_token_type": subject_token_type,
        }

        if service_account_impersonation_url:
            # Service account impersonation request/response.
            expire_time = (
                _helpers.utcnow().replace(microsecond=0)
                + datetime.timedelta(seconds=3600)
            ).isoformat("T") + "Z"
            impersonation_response = {
                "accessToken": "SA_ACCESS_TOKEN",
                "expireTime": expire_time,
            }
            impersonation_headers = {
                "Content-Type": "application/json",
                "authorization": "Bearer {}".format(token_response["access_token"]),
            }
            impersonation_request_data = {
                "delegates": None,
                "scope": used_scopes,
                "lifetime": "3600s",
            }

        # Initialize mock request to handle token retrieval, token exchange and
        # service account impersonation request.
        requests = []
        if credential_data:
            requests.append((http.client.OK, credential_data))

        token_request_index = len(requests)
        requests.append((http.client.OK, token_response))

        if service_account_impersonation_url:
            impersonation_request_index = len(requests)
            requests.append((http.client.OK, impersonation_response))

        request = cls.make_mock_request(*[el for req in requests for el in req])

        credentials.refresh(request)

        assert len(request.call_args_list) == len(requests)
        if credential_data:
            cls.assert_credential_request_kwargs(request.call_args_list[0][1], None)
        # Verify token exchange request parameters.
        cls.assert_token_request_kwargs(
            request.call_args_list[token_request_index][1],
            token_headers,
            token_request_data,
            token_url,
        )
        # Verify service account impersonation request parameters if the request
        # is processed.
        if service_account_impersonation_url:
            cls.assert_impersonation_request_kwargs(
                request.call_args_list[impersonation_request_index][1],
                impersonation_headers,
                impersonation_request_data,
                service_account_impersonation_url,
            )
            assert credentials.token == impersonation_response["accessToken"]
        else:
            assert credentials.token == token_response["access_token"]
        assert credentials.quota_project_id == quota_project_id
        assert credentials.scopes == scopes
        assert credentials.default_scopes == default_scopes

    @classmethod
    def make_credentials(
        cls,
        client_id=None,
        client_secret=None,
        quota_project_id=None,
        scopes=None,
        default_scopes=None,
        service_account_impersonation_url=None,
        credential_source=None,
    ):
        return identity_pool.Credentials(
            audience=AUDIENCE,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=service_account_impersonation_url,
            credential_source=credential_source,
            client_id=client_id,
            client_secret=client_secret,
            quota_project_id=quota_project_id,
            scopes=scopes,
            default_scopes=default_scopes,
        )

    @mock.patch.object(identity_pool.Credentials, "__init__", return_value=None)
    def test_from_info_full_options(self, mock_init):
        credentials = identity_pool.Credentials.from_info(
            {
                "audience": AUDIENCE,
                "subject_token_type": SUBJECT_TOKEN_TYPE,
                "token_url": TOKEN_URL,
                "service_account_impersonation_url": SERVICE_ACCOUNT_IMPERSONATION_URL,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "quota_project_id": QUOTA_PROJECT_ID,
                "credential_source": self.CREDENTIAL_SOURCE_TEXT,
            }
        )

        # Confirm identity_pool.Credentials instantiated with expected attributes.
        assert isinstance(credentials, identity_pool.Credentials)
        mock_init.assert_called_once_with(
            audience=AUDIENCE,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            quota_project_id=QUOTA_PROJECT_ID,
        )

    @mock.patch.object(identity_pool.Credentials, "__init__", return_value=None)
    def test_from_info_required_options_only(self, mock_init):
        credentials = identity_pool.Credentials.from_info(
            {
                "audience": AUDIENCE,
                "subject_token_type": SUBJECT_TOKEN_TYPE,
                "token_url": TOKEN_URL,
                "credential_source": self.CREDENTIAL_SOURCE_TEXT,
            }
        )

        # Confirm identity_pool.Credentials instantiated with expected attributes.
        assert isinstance(credentials, identity_pool.Credentials)
        mock_init.assert_called_once_with(
            audience=AUDIENCE,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            client_id=None,
            client_secret=None,
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            quota_project_id=None,
        )

    @mock.patch.object(identity_pool.Credentials, "__init__", return_value=None)
    def test_from_file_full_options(self, mock_init, tmpdir):
        info = {
            "audience": AUDIENCE,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "token_url": TOKEN_URL,
            "service_account_impersonation_url": SERVICE_ACCOUNT_IMPERSONATION_URL,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "quota_project_id": QUOTA_PROJECT_ID,
            "credential_source": self.CREDENTIAL_SOURCE_TEXT,
        }
        config_file = tmpdir.join("config.json")
        config_file.write(json.dumps(info))
        credentials = identity_pool.Credentials.from_file(str(config_file))

        # Confirm identity_pool.Credentials instantiated with expected attributes.
        assert isinstance(credentials, identity_pool.Credentials)
        mock_init.assert_called_once_with(
            audience=AUDIENCE,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            quota_project_id=QUOTA_PROJECT_ID,
        )

    @mock.patch.object(identity_pool.Credentials, "__init__", return_value=None)
    def test_from_file_required_options_only(self, mock_init, tmpdir):
        info = {
            "audience": AUDIENCE,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "token_url": TOKEN_URL,
            "credential_source": self.CREDENTIAL_SOURCE_TEXT,
        }
        config_file = tmpdir.join("config.json")
        config_file.write(json.dumps(info))
        credentials = identity_pool.Credentials.from_file(str(config_file))

        # Confirm identity_pool.Credentials instantiated with expected attributes.
        assert isinstance(credentials, identity_pool.Credentials)
        mock_init.assert_called_once_with(
            audience=AUDIENCE,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            client_id=None,
            client_secret=None,
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            quota_project_id=None,
        )

    def test_constructor_invalid_options(self):
        credential_source = {"unsupported": "value"}

        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source=credential_source)

        assert excinfo.match(r"Missing credential_source")

    def test_constructor_invalid_options_url_and_file(self):
        credential_source = {
            "url": self.CREDENTIAL_URL,
            "file": SUBJECT_TOKEN_TEXT_FILE,
        }

        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source=credential_source)

        assert excinfo.match(r"Ambiguous credential_source")

    def test_constructor_invalid_options_environment_id(self):
        credential_source = {"url": self.CREDENTIAL_URL, "environment_id": "aws1"}

        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source=credential_source)

        assert excinfo.match(
            r"Invalid Identity Pool credential_source field 'environment_id'"
        )

    def test_constructor_invalid_credential_source(self):
        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source="non-dict")

        assert excinfo.match(r"Missing credential_source")

    def test_constructor_invalid_credential_source_format_type(self):
        credential_source = {"format": {"type": "xml"}}

        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source=credential_source)

        assert excinfo.match(r"Invalid credential_source format 'xml'")

    def test_constructor_missing_subject_token_field_name(self):
        credential_source = {"format": {"type": "json"}}

        with pytest.raises(ValueError) as excinfo:
            self.make_credentials(credential_source=credential_source)

        assert excinfo.match(
            r"Missing subject_token_field_name for JSON credential_source format"
        )

    def test_info_with_file_credential_source(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_TEXT_URL.copy()
        )

        assert credentials.info == {
            "type": "external_account",
            "audience": AUDIENCE,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "token_url": TOKEN_URL,
            "credential_source": self.CREDENTIAL_SOURCE_TEXT_URL,
        }

    def test_info_with_url_credential_source(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_JSON_URL.copy()
        )

        assert credentials.info == {
            "type": "external_account",
            "audience": AUDIENCE,
            "subject_token_type": SUBJECT_TOKEN_TYPE,
            "token_url": TOKEN_URL,
            "credential_source": self.CREDENTIAL_SOURCE_JSON_URL,
        }

    def test_retrieve_subject_token_missing_subject_token(self, tmpdir):
        # Provide empty text file.
        empty_file = tmpdir.join("empty.txt")
        empty_file.write("")
        credential_source = {"file": str(empty_file)}
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(None)

        assert excinfo.match(r"Missing subject_token in the credential_source file")

    def test_retrieve_subject_token_text_file(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_TEXT
        )

        subject_token = credentials.retrieve_subject_token(None)

        assert subject_token == TEXT_FILE_SUBJECT_TOKEN

    def test_retrieve_subject_token_json_file(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_JSON
        )

        subject_token = credentials.retrieve_subject_token(None)

        assert subject_token == JSON_FILE_SUBJECT_TOKEN

    def test_retrieve_subject_token_json_file_invalid_field_name(self):
        credential_source = {
            "file": SUBJECT_TOKEN_JSON_FILE,
            "format": {"type": "json", "subject_token_field_name": "not_found"},
        }
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(None)

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                SUBJECT_TOKEN_JSON_FILE, "not_found"
            )
        )

    def test_retrieve_subject_token_invalid_json(self, tmpdir):
        # Provide JSON file. This should result in JSON parsing error.
        invalid_json_file = tmpdir.join("invalid.json")
        invalid_json_file.write("{")
        credential_source = {
            "file": str(invalid_json_file),
            "format": {"type": "json", "subject_token_field_name": "access_token"},
        }
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(None)

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                str(invalid_json_file), "access_token"
            )
        )

    def test_retrieve_subject_token_file_not_found(self):
        credential_source = {"file": "./not_found.txt"}
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(None)

        assert excinfo.match(r"File './not_found.txt' was not found")

    def test_refresh_text_file_success_without_impersonation_ignore_default_scopes(
        self,
    ):
        credentials = self.make_credentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            scopes=SCOPES,
            # Default scopes should be ignored.
            default_scopes=["ignored"],
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            basic_auth_encoding=BASIC_AUTH_ENCODING,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=["ignored"],
        )

    def test_refresh_text_file_success_without_impersonation_use_default_scopes(self):
        credentials = self.make_credentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            scopes=None,
            # Default scopes should be used since user specified scopes are none.
            default_scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            basic_auth_encoding=BASIC_AUTH_ENCODING,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=None,
            default_scopes=SCOPES,
        )

    def test_refresh_text_file_success_with_impersonation_ignore_default_scopes(self):
        # Initialize credentials with service account impersonation and basic auth.
        credentials = self.make_credentials(
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            scopes=SCOPES,
            # Default scopes should be ignored.
            default_scopes=["ignored"],
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            basic_auth_encoding=None,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=["ignored"],
        )

    def test_refresh_text_file_success_with_impersonation_use_default_scopes(self):
        # Initialize credentials with service account impersonation, basic auth
        # and default scopes (no user scopes).
        credentials = self.make_credentials(
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            scopes=None,
            # Default scopes should be used since user specified scopes are none.
            default_scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            basic_auth_encoding=None,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=None,
            default_scopes=SCOPES,
        )

    def test_refresh_json_file_success_without_impersonation(self):
        credentials = self.make_credentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            # Test with JSON format type.
            credential_source=self.CREDENTIAL_SOURCE_JSON,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=JSON_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            basic_auth_encoding=BASIC_AUTH_ENCODING,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
        )

    def test_refresh_json_file_success_with_impersonation(self):
        # Initialize credentials with service account impersonation and basic auth.
        credentials = self.make_credentials(
            # Test with JSON format type.
            credential_source=self.CREDENTIAL_SOURCE_JSON,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=JSON_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            basic_auth_encoding=None,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
        )

    def test_refresh_with_retrieve_subject_token_error(self):
        credential_source = {
            "file": SUBJECT_TOKEN_JSON_FILE,
            "format": {"type": "json", "subject_token_field_name": "not_found"},
        }
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.refresh(None)

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                SUBJECT_TOKEN_JSON_FILE, "not_found"
            )
        )

    def test_retrieve_subject_token_from_url(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_TEXT_URL
        )
        request = self.make_mock_request(token_data=TEXT_FILE_SUBJECT_TOKEN)
        subject_token = credentials.retrieve_subject_token(request)

        assert subject_token == TEXT_FILE_SUBJECT_TOKEN
        self.assert_credential_request_kwargs(request.call_args_list[0][1], None)

    def test_retrieve_subject_token_from_url_with_headers(self):
        credentials = self.make_credentials(
            credential_source={"url": self.CREDENTIAL_URL, "headers": {"foo": "bar"}}
        )
        request = self.make_mock_request(token_data=TEXT_FILE_SUBJECT_TOKEN)
        subject_token = credentials.retrieve_subject_token(request)

        assert subject_token == TEXT_FILE_SUBJECT_TOKEN
        self.assert_credential_request_kwargs(
            request.call_args_list[0][1], {"foo": "bar"}
        )

    def test_retrieve_subject_token_from_url_json(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_JSON_URL
        )
        request = self.make_mock_request(token_data=JSON_FILE_CONTENT)
        subject_token = credentials.retrieve_subject_token(request)

        assert subject_token == JSON_FILE_SUBJECT_TOKEN
        self.assert_credential_request_kwargs(request.call_args_list[0][1], None)

    def test_retrieve_subject_token_from_url_json_with_headers(self):
        credentials = self.make_credentials(
            credential_source={
                "url": self.CREDENTIAL_URL,
                "format": {"type": "json", "subject_token_field_name": "access_token"},
                "headers": {"foo": "bar"},
            }
        )
        request = self.make_mock_request(token_data=JSON_FILE_CONTENT)
        subject_token = credentials.retrieve_subject_token(request)

        assert subject_token == JSON_FILE_SUBJECT_TOKEN
        self.assert_credential_request_kwargs(
            request.call_args_list[0][1], {"foo": "bar"}
        )

    def test_retrieve_subject_token_from_url_not_found(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_TEXT_URL
        )
        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(
                self.make_mock_request(token_status=404, token_data=JSON_FILE_CONTENT)
            )

        assert excinfo.match("Unable to retrieve Identity Pool subject token")

    def test_retrieve_subject_token_from_url_json_invalid_field(self):
        credential_source = {
            "url": self.CREDENTIAL_URL,
            "format": {"type": "json", "subject_token_field_name": "not_found"},
        }
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(
                self.make_mock_request(token_data=JSON_FILE_CONTENT)
            )

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                self.CREDENTIAL_URL, "not_found"
            )
        )

    def test_retrieve_subject_token_from_url_json_invalid_format(self):
        credentials = self.make_credentials(
            credential_source=self.CREDENTIAL_SOURCE_JSON_URL
        )

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.retrieve_subject_token(self.make_mock_request(token_data="{"))

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                self.CREDENTIAL_URL, "access_token"
            )
        )

    def test_refresh_text_file_success_without_impersonation_url(self):
        credentials = self.make_credentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT_URL,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            basic_auth_encoding=BASIC_AUTH_ENCODING,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
            credential_data=TEXT_FILE_SUBJECT_TOKEN,
        )

    def test_refresh_text_file_success_with_impersonation_url(self):
        # Initialize credentials with service account impersonation and basic auth.
        credentials = self.make_credentials(
            # Test with text format type.
            credential_source=self.CREDENTIAL_SOURCE_TEXT_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=TEXT_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            basic_auth_encoding=None,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
            credential_data=TEXT_FILE_SUBJECT_TOKEN,
        )

    def test_refresh_json_file_success_without_impersonation_url(self):
        credentials = self.make_credentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            # Test with JSON format type.
            credential_source=self.CREDENTIAL_SOURCE_JSON_URL,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=JSON_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=None,
            basic_auth_encoding=BASIC_AUTH_ENCODING,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
            credential_data=JSON_FILE_CONTENT,
        )

    def test_refresh_json_file_success_with_impersonation_url(self):
        # Initialize credentials with service account impersonation and basic auth.
        credentials = self.make_credentials(
            # Test with JSON format type.
            credential_source=self.CREDENTIAL_SOURCE_JSON_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            scopes=SCOPES,
        )

        self.assert_underlying_credentials_refresh(
            credentials=credentials,
            audience=AUDIENCE,
            subject_token=JSON_FILE_SUBJECT_TOKEN,
            subject_token_type=SUBJECT_TOKEN_TYPE,
            token_url=TOKEN_URL,
            service_account_impersonation_url=SERVICE_ACCOUNT_IMPERSONATION_URL,
            basic_auth_encoding=None,
            quota_project_id=None,
            used_scopes=SCOPES,
            scopes=SCOPES,
            default_scopes=None,
            credential_data=JSON_FILE_CONTENT,
        )

    def test_refresh_with_retrieve_subject_token_error_url(self):
        credential_source = {
            "url": self.CREDENTIAL_URL,
            "format": {"type": "json", "subject_token_field_name": "not_found"},
        }
        credentials = self.make_credentials(credential_source=credential_source)

        with pytest.raises(exceptions.RefreshError) as excinfo:
            credentials.refresh(self.make_mock_request(token_data=JSON_FILE_CONTENT))

        assert excinfo.match(
            "Unable to parse subject_token from JSON file '{}' using key '{}'".format(
                self.CREDENTIAL_URL, "not_found"
            )
        )
