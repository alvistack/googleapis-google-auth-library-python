# Copyright 2023 Wong Hoi Sing Edison <hswong3i@pantarei-design.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

%global debug_package %{nil}

Name: python-google-auth
Epoch: 100
Version: 2.23.0
Release: 1%{?dist}
BuildArch: noarch
Summary: Google Auth Python Library
License: Apache-2.0
URL: https://github.com/googleapis/google-auth-library-python/tags
Source0: %{name}_%{version}.orig.tar.gz
BuildRequires: fdupes
BuildRequires: python-rpm-macros
BuildRequires: python3-devel
BuildRequires: python3-setuptools

%description
This library simplifies using Google's various server-to-server
authentication mechanisms to access Google APIs.

%prep
%autosetup -T -c -n %{name}_%{version}-%{release}
tar -zx -f %{S:0} --strip-components=1 -C .

%build
%py3_build

%install
%py3_install
find %{buildroot}%{python3_sitelib} -type f -name '*.pyc' -exec rm -rf {} \;
fdupes -qnrps %{buildroot}%{python3_sitelib}

%check

%if 0%{?suse_version} > 1500
%package -n python%{python3_version_nodots}-google-auth
Summary: Google Auth Python Library
Requires: python3
Requires: python3-cachetools >= 2.0.0
Requires: python3-pyasn1 >= 0.2.1
Requires: python3-rsa >= 3.1.4
Requires: python3-urllib3 < 100:2.0
Provides: python3-google-auth = %{epoch}:%{version}-%{release}
Provides: python3dist(google-auth) = %{epoch}:%{version}-%{release}
Provides: python%{python3_version}-google-auth = %{epoch}:%{version}-%{release}
Provides: python%{python3_version}dist(google-auth) = %{epoch}:%{version}-%{release}
Provides: python%{python3_version_nodots}-google-auth = %{epoch}:%{version}-%{release}
Provides: python%{python3_version_nodots}dist(google-auth) = %{epoch}:%{version}-%{release}

%description -n python%{python3_version_nodots}-google-auth
This library simplifies using Google's various server-to-server
authentication mechanisms to access Google APIs.

%files -n python%{python3_version_nodots}-google-auth
%license LICENSE
%{python3_sitelib}/*
%endif

%if !(0%{?suse_version} > 1500)
%package -n python3-google-auth
Summary: Google Auth Python Library
Requires: python3
Requires: python3-cachetools >= 2.0.0
Requires: python3-pyasn1 >= 0.2.1
Requires: python3-rsa >= 3.1.4
Requires: python3-urllib3 < 100:2.0
Provides: python3-google-auth = %{epoch}:%{version}-%{release}
Provides: python3dist(google-auth) = %{epoch}:%{version}-%{release}
Provides: python%{python3_version}-google-auth = %{epoch}:%{version}-%{release}
Provides: python%{python3_version}dist(google-auth) = %{epoch}:%{version}-%{release}
Provides: python%{python3_version_nodots}-google-auth = %{epoch}:%{version}-%{release}
Provides: python%{python3_version_nodots}dist(google-auth) = %{epoch}:%{version}-%{release}

%description -n python3-google-auth
This library simplifies using Google's various server-to-server
authentication mechanisms to access Google APIs.

%files -n python3-google-auth
%license LICENSE
%{python3_sitelib}/*
%endif

%changelog
