# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright 2013 Citrix Systems
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# @author: Youcef Laribi, Citrix

import sys
import httplib
import base64

from urlparse import urlparse

from neutron.common import exceptions as q_exc
from neutron.services.loadbalancer.drivers.netscaler.serialization import Serializer
from neutron.openstack.common import log as logging

LOG = logging.getLogger(__name__)

class RESTClient:

    def __init__(self, uri, username, password, plurals=None, headers=None, is_proxy = False):
        
        if plurals:
            self.plurals = plurals
        else:
            self.plurals = {}
        self.is_proxy = is_proxy
        
        if not uri:
            LOG.debug(_("No uri passed. Cannot connect"))
            raise Exception("No uri passed. Cannot connect")
        
        self.headers={}
        if headers != None:
            self.headers = headers
           
        parts = urlparse(uri)
        self.uri = uri
        host_port_parts = parts.netloc.split(':')
        
        self.port = None
        
        if len(host_port_parts) > 1:
            self.host = host_port_parts[0]
            self.port = host_port_parts[1]
        else:
            self.host = host_port_parts[0]

        if type(self.host).__name__ == 'unicode':
            self.host = self.host.encode('ascii','ignore')
        
        if self.port and type(self.port).__name__ == 'unicode':
            self.port = self.port.encode('ascii','ignore')


        if parts.scheme.lower() == "http":   
            self.protocol = "http"
            if not self.port:

                self.port = 80     


        elif parts.scheme.lower() == "https":
            self.protocol = "https"
            if not self.port:
                self.port = 443

        else:
            LOG.error(_("scheme in uri is unrecognized:%s" % parts.scheme))
            raise q_exc.ServiceUnavailable()
            
        self.service_path = parts.path
        
        self.auth = None

        if username != None and password != None: 
            base64string = base64.encodestring("%s:%s" % (username, password))
            base64string = base64string[:-1]
            self.auth = 'Basic %s' % base64string


    def _get_connection(self):

        if self.protocol == "http":
            connection = httplib.HTTPConnection(self.host, self.port)     
        elif self.protocol == "https":
            connection = httplib.HTTPSConnection(self.host, self.port)
        else:
            LOG.error(_("protocol unrecognized:%s" % self.protocol))
            raise q_exc.ServiceUnavailable()

        return connection


    def _is_valid_response(self, response_status):
        if response_status < httplib.BAD_REQUEST: # startus is less than 400, the response is fine
            return True
        else:
            return False
        
    def update_headers(self, headers):
        if headers:
            self.headers.update(headers)


    def _get_response_dict(self, response, serializer):
        response_status = response.status
        response_body = response.read()
        response_headers = response.getheaders()
        
        response_dict = {}
        response_dict['status'] = response_status 
        response_dict['body'] = response_body
        response_dict['headers'] =  response_headers
        
        if self._is_valid_response(response_status):
            if len(response_body) > 0:
                if serializer != None:
                    parsed_body = serializer.deserialize(response_body, "xml")
                    response_dict['dict'] = parsed_body
                else:
                    response_dict['dict'] = response_body

        return response_dict

        
    def create_resource(self, resource_path, object_name, object_data):

        method = 'POST'
        headers = {'Content-Type':'application/xml', 'Accept':'application/xml', 'Authorization' : self.auth}
        if self.headers:
            headers.update(self.headers)

        
        request_body=""
        
        serializer = None
        if isinstance(object_data, str):
            request_body = object_data
        elif object_data:
            serializer = Serializer(plurals=self.plurals)
        
        if serializer != None:
            request_body = serializer.serialize(object_name, object_data, "xml")
        
        
        url_path = self.service_path + "/" + resource_path

        LOG.debug(_("Request %s %s" %(method, url_path)))

        if len(request_body) > 0:
            LOG.debug(_("Auth:%s requestbody: %s" % (self.auth, request_body)))
            
        LOG.debug(_("Headers in create_resource %s " % repr(headers)))

        try:

            connection = self._get_connection()

            connection.request(method, url_path, body=request_body, headers=headers)

            response = connection.getresponse()

            connection.close()
            
            resp_dict = self._get_response_dict(response, serializer)
            
            LOG.debug(_("Response: %s" % (resp_dict['body'])))

            response_status = resp_dict['status']
            LOG.debug(_("response_status: %s" % (response_status)))
            
            if self.is_proxy:
                return response_status, resp_dict
            
            if str(response_status) == "401":
                LOG.error(_("Unable to login.Invalid credentials passed for host: %s." % (self.host)))
                raise q_exc.ServiceUnavailable()
            
            if not self._is_valid_response(response_status):
                LOG.error(_("Failed to create %s in %s, status: %s" % (url_path, self.uri , response_status)))
                raise q_exc.ServiceUnavailable()
            
            return response_status, resp_dict
        
        except (LookupError, ImportError) as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            LOG.error(_("Error while connecting to %s :  %s") % (self.uri, exc_type))
            raise q_exc.ServiceUnavailable()


    
    
    def retrieve_resource(self, resource_path, parse_response=True):

        method = 'GET'
        headers = {'Content-Type':'application/xml', 'Accept':'application/xml', 'Authorization' : self.auth}
        if self.headers:
            headers.update(self.headers)
        
        url_path = self.service_path + "/" + resource_path

        LOG.debug(_("Request %s %s" %(method, url_path)))

        LOG.debug(_("Headers used for request %s" % (str(headers))))

        connection = self._get_connection()

        connection.request(method, url_path, headers=headers)
        
        LOG.debug(_("Plurals used for parsing %s" % (str(self.plurals))))
        
        serializer = None
        if parse_response:
            serializer = Serializer(plurals=self.plurals)
        
        LOG.debug(_("Retrieve resource_path: %s" % (url_path)))
        
        try:
        
            response = connection.getresponse()

            connection.close()

            resp_dict = self._get_response_dict(response, serializer)
            
            response_status = resp_dict['status']
            
            LOG.debug(_("Response: %s" % (resp_dict['body'])))
            
            if self.is_proxy:
                return response_status, resp_dict
            
            if str(response_status) == "401":
                LOG.error(_("Unable to login.Invalid credentials passed for host: %s." % (self.host)))
                raise q_exc.ServiceUnavailable()
            
        except (LookupError, ImportError) as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            LOG.error(_("Error while connecting to %s :  %s") % (self.uri, exc_type))
            raise q_exc.ServiceUnavailable()

        return resp_dict['status'], resp_dict


    def update_resource(self, resource_path, object_name, object_data):

        method = 'PUT'
        headers = {'Content-Type':'application/xml', 'Accept':'application/xml', 'Authorization' : self.auth}
        if self.headers:
            headers.update(self.headers)
        serializer = None
        if isinstance(object_data, str):
            request_body = object_data
        elif object_data:
            serializer = Serializer(plurals=self.plurals)
        
        if serializer != None:
            request_body = serializer.serialize(object_name, object_data, "xml")

        url_path = self.service_path + "/" + resource_path
    
        LOG.debug(_("Auth:%s url_path:%s requestbody: %s" % (self.auth,url_path, request_body)))
        
        try:

            connection = self._get_connection()

            connection.request(method, url_path, body=request_body, headers=headers)

            response = connection.getresponse()

            connection.close()

            resp_dict = self._get_response_dict(response, serializer)

            LOG.debug(_("Response: %s" % (resp_dict['body'])))

            response_status = resp_dict['status']

            if self.is_proxy:
                return response_status, resp_dict
            
            if str(response_status) == "401":
                LOG.error(_("Unable to login.Invalid credentials passed for host: %s." % (self.host)))
                raise q_exc.ServiceUnavailable()

            if not self._is_valid_response(response_status):
                LOG.error(_("Failed to update %s in %s, status: %s" % (url_path, self.uri , response_status)))
                raise q_exc.ServiceUnavailable()

            return response_status, resp_dict

        except (LookupError, ImportError) as e:
            exc_type, exc_value, exc_tb = sys.exc_info()
            LOG.error(_("Error while connecting to %s :  %s") % (self.uri, exc_type))
            raise q_exc.ServiceUnavailable()
    

    
    def remove_resource(self, resource_path, parse_response=True):
        
        method = 'DELETE'
        headers = {'Content-Type':'application/xml', 'Accept':'application/xml', 'Authorization' : self.auth}
        if self.headers:
            headers.update(self.headers)

        url_path = self.service_path + "/" + resource_path
        
        LOG.debug(_("Request %s %s" %(method, url_path)))

        LOG.debug(_("Headers used for request %s" % (str(headers))))

        connection = self._get_connection()

        connection.request(method, url_path, headers=headers)

        response = connection.getresponse()

        connection.close()

        serializer = None

        if parse_response:
            serializer = Serializer(plurals=self.plurals)
            

        resp_dict = self._get_response_dict(response, serializer)

        LOG.debug(_("Response: %s" % (resp_dict['body'])))

        response_status = resp_dict['status']

        LOG.debug(_("Response status %s" % (str(response_status))))
        
        if self.is_proxy:
            return response_status, resp_dict


        if str(response_status) == "401":
            LOG.error(_("Unable to login.Invalid credentials passed for host: %s." % (self.host)))
            raise q_exc.ServiceUnavailable()
            
        if not self._is_valid_response(response_status):
            LOG.error(_("Failed to remove %s in %s, status: %s" % (url_path, self.uri , response_status)))
            raise q_exc.ServiceUnavailable()

        return response_status, resp_dict


        
