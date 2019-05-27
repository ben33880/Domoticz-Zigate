
import Domoticz
import json
import os
import os.path

import mimetypes
from urllib.parse import urlparse, urlsplit, urldefrag, parse_qs

from time import time, ctime, strftime, gmtime, mktime, strptime
import zlib
import gzip
from Modules.consts import ADDRESS_MODE, MAX_LOAD_ZIGATE, ZCL_CLUSTERS_LIST
from Classes.PluginConf import SETTINGS
from Modules.output import ZigatePermitToJoin, NwkMgtUpdReq


DELAY = 0
ALLOW_GZIP = 1
ALLOW_DEFLATE = 1
ALLOW_CHUNK = 0
MAX_KB_TO_SEND = 4 * 1024
DEBUG_HTTP = False

class WebServer(object):
    hearbeats = 0 

    def __init__( self, ZigateData, PluginParameters, PluginConf, Statistics, adminWidgets, ZigateComm, HomeDirectory, hardwareID, groupManagement, Devices, ListOfDevices, IEEE2NWK , permitTojoin):

        self.httpServerConn = None
        self.httpsServerConn = None
        self.httpServerConns = {}
        self.httpClientConn = None

        self.pluginconf = PluginConf
        self.zigatedata = ZigateData
        self.adminWidget = adminWidgets
        self.ZigateComm = ZigateComm
        self.statistics = Statistics
        self.pluginparameters = PluginParameters

        self.permitTojoin = permitTojoin

        if groupManagement:
            self.groupmgt = groupManagement
        else:
            self.groupmgt = None
        self.ListOfDevices = ListOfDevices
        self.IEEE2NWK = IEEE2NWK
        self.Devices = Devices

        self.homedirectory = HomeDirectory
        self.hardwareID = hardwareID
        mimetypes.init()
        self.startWebServer()
        

    def  startWebServer( self ):

        self.httpServerConn = Domoticz.Connection(Name="Zigate Server Connection", Transport="TCP/IP", Protocol="HTTP", Port='9440')
        self.httpServerConn.Listen()
        Domoticz.Log("Web backend started")


    def onConnect(self, Connection, Status, Description):

        if (Status == 0):
            Domoticz.Log("Connected successfully to: "+Connection.Address+":"+Connection.Port)
            if Connection.Name not in self.httpServerConns:
                self.httpServerConns[Connection.Name] = Connection
            else:
                Domoticz.Log("Connection already established .... %s" %Connection)
        else:
            Domoticz.Log("Failed to connect ("+str(Status)+") to: "+Connection.Address+":"+Connection.Port+" with error: "+Description)
        Domoticz.Log("Number of Connection : %s" %len(self.httpServerConns))

    def onDisconnect ( self, Connection ):

        Domoticz.Log("onDisconnect %s" %(Connection))
        for x in self.httpServerConns:
            Domoticz.Log("--> "+str(x)+"'.")
        if Connection.Name in self.httpServerConns:
            del self.httpServerConns[Connection.Name]


    def onMessage( self, Connection, Data ):

            Domoticz.Log("WebServer onMessage")
            DumpHTTPResponseToLog(Data)

            headerCode = "200 OK"
            if (not 'Verb' in Data):
                Domoticz.Error("Invalid web request received, no Verb present")
                headerCode = "400 Bad Request"
            elif (Data['Verb'] not in ( 'GET', 'PUT', 'POST', 'DELETE')):
                Domoticz.Error("Invalid web request received, only GET requests allowed ("+Data['Verb']+")")
                headerCode = "405 Method Not Allowed"
            elif (not 'URL' in Data):
                Domoticz.Error("Invalid web request received, no URL present")
                headerCode = "400 Bad Request"

            parsed_url = urlparse(  Data['URL'] )
            Domoticz.Log("URL: %s , Path: %s" %( Data['URL'], parsed_url.path))
            if  Data['URL'][0] == '/': parsed_query = Data['URL'][1:].split('/')
            else: parsed_query = Data['URL'].split('/')

            if 'Data' not in Data: Data['Data'] = None

            if (headerCode != "200 OK"):
                self.sendResponse( Connection, {"Status": headerCode} )
                return
            elif ( parsed_query[0] == 'rest-zigate'):
                Domoticz.Log("Receiving a REST API - Version: %s, Verb: %s, Command: %s, Param: %s" \
                        %( parsed_query[1], Data['Verb'],  parsed_query[2], parsed_query[3:] ))
                if parsed_query[1] == '1':
                    # API Version 1
                    self.do_rest( Connection, Data['Verb'], Data['Data'], parsed_query[1], parsed_query[2], parsed_query[3:])
                else:
                    Domoticz.Error("Unknown API version %s" %parsed_query[1])
                    headerCode = "400 Bad Request"
                return

            # Finaly we simply has to serve a File.
            webFilename = self.homedirectory +'www'+ Data['URL']
            Domoticz.Log("webFilename: %s" %webFilename)
            if not os.path.isfile( webFilename ):
                webFilename =  self.homedirectory + 'www' + "/index.html"
                Domoticz.Log("Redirecting to /index.html")

            # We are ready to send the response
            _response = setupHeadersResponse()
            _response["Headers"]["Cache-Control"] = "private"

            Domoticz.Log("Opening: %s" %webFilename)
            currentVersionOnServer = os.path.getmtime(webFilename)
            _lastmodified = strftime("%a, %d %m %y %H:%M:%S GMT", gmtime(currentVersionOnServer))


            # Can we use Cache if exists
            if 'If-Modified-Since' in Data['Headers']:
                lastVersionInCache = Data['Headers']['If-Modified-Since']
                Domoticz.Log("InCache: %s versus Current: %s" %(lastVersionInCache, _lastmodified))
                if lastVersionInCache == _lastmodified:
                    # No need to send it back
                    Domoticz.Log("Use Caching")
                    _response['Status'] = "304 Not Modified"
                    self.sendResponse( Connection, _response )
                    return _response

            if 'Ranges' in Data['Headers']:
                Domoticz.Log("Ranges processing")
                range = Data['Headers']['Range']
                fileStartPosition = int(range[range.find('=')+1:range.find('-')])
                messageFileSize = os.path.getsize(webFilename)
                messageFile = open(webFilename, mode='rb')
                messageFile.seek(fileStartPosition)
                fileContent = messageFile.read(MAX_KB_TO_SEND)
                Domoticz.Log(Connection.Address+":"+Connection.Port+" Sent 'GET' request file '"+Data['URL']+"' from position "+str(fileStartPosition)+", "+str(len(fileContent))+" bytes will be returned")
                _response["Status"] = "200 OK"
                if (len(fileContent) == MAX_KB_TO_SEND):
                    _response["Status"] = "206 Partial Content"
                    _response["Headers"]["Content-Range"] = "bytes "+str(fileStartPosition)+"-"+str(messageFile.tell())+"/"+str(messageFileSize)
                DumpHTTPResponseToLog( _response )
                Connection.Send( _response)
            else:
                _response["Headers"]["Last-Modified"] = _lastmodified
                with open(webFilename , mode ='rb') as webFile:
                    _response["Data"] = webFile.read()
    
                _contentType, _contentEncoding = mimetypes.guess_type( Data['URL'] )
                #if ( webFilename.find('.js') != -1): _contentType = 'text/javascript'
     
                if _contentType:
                    _response["Headers"]["Content-Type"] = _contentType +"; charset=utf-8"
                if _contentEncoding:
                    _response["Headers"]["Content-Encoding"] = _contentEncoding 
     
                _response["Status"] = "200 OK"
                if 'Cookie' in Data['Headers']: 
                    _response['Headers']['Cookie'] = Data['Headers']['Cookie']
    
                if 'Accept-Encoding' in Data['Headers']:
                    self.sendResponse( Connection, _response, AcceptEncoding = Data['Headers']['Accept-Encoding']  )
                else:
                    self.sendResponse( Connection, _response )


    def sendResponse( self, Connection, Response, AcceptEncoding=None ):

        if ('Data' not in Response) or (Response['Data'] == None):
            DumpHTTPResponseToLog( Response )
            Connection.Send( Response , Delay= DELAY)
            return

        Domoticz.Log("Sending Response to : %s" %(Connection.Name))

        # Compression
        if (ALLOW_GZIP or ALLOW_DEFLATE ) and 'Data' in Response and AcceptEncoding:
            Domoticz.Log("sendResponse - Accept-Encoding: %s, Chunk: %s, Deflate: %s , Gzip: %s" %(AcceptEncoding, ALLOW_CHUNK, ALLOW_DEFLATE, ALLOW_GZIP))
            if len(Response["Data"]) > MAX_KB_TO_SEND:
                orig_size = len(Response["Data"])
                if ALLOW_DEFLATE and AcceptEncoding.find('deflate') != -1:
                    Domoticz.Log("Compressing - deflate")
                    zlib_compress = zlib.compressobj( 9, zlib.DEFLATED, -zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, 2)
                    deflated = zlib_compress.compress(Response["Data"])
                    deflated += zlib_compress.flush()
                    Response["Headers"]['Content-Encoding'] = 'deflate'
                    Response["Data"] = deflated

                elif ALLOW_GZIP and AcceptEncoding.find('gzip') != -1:
                    Domoticz.Log("Compressing - gzip")
                    Response["Data"] = gzip.compress( Response["Data"] )
                    Response["Headers"]['Content-Encoding'] = 'gzip'

                Domoticz.Log("Compression from %s to %s (%s %%)" %( orig_size, len(Response["Data"]), int(100-(len(Response["Data"])/orig_size)*100)))

        # Chunking, Follow the Domoticz Python Plugin Framework
        if ALLOW_CHUNK and len(Response['Data']) > MAX_KB_TO_SEND:
            idx = 0
            HTTPchunk = {}
            HTTPchunk['Status'] = Response['Status']
            HTTPchunk['Chunk'] = True
            HTTPchunk['Headers'] = {}
            HTTPchunk['Headers'] = dict(Response['Headers'])
            HTTPchunk['Data'] = Response['Data'][0:MAX_KB_TO_SEND]
            Domoticz.Log("Sending: %s out of %s" %(idx, len((Response['Data']))))

            # Firs Chunk
            DumpHTTPResponseToLog( HTTPchunk )
            Connection.Send( HTTPchunk , Delay= DELAY)

            idx = MAX_KB_TO_SEND
            while idx != -1:
                tosend={}
                tosend['Chunk'] = True
                if idx + MAX_KB_TO_SEND < len(Response['Data']):
                    # we have to send one chunk and then continue
                    tosend['Data'] = Response['Data'][idx:idx+MAX_KB_TO_SEND]        
                    idx += MAX_KB_TO_SEND
                else:
                    # Last Chunk with Data
                    tosend['Data'] = Response['Data'][idx:]        
                    idx = -1

                Domoticz.Log("Sending Chunk: %s out of %s" %(idx, len((Response['Data']))))
                Connection.Send( tosend , Delay= DELAY)

            # Closing Chunk
            tosend={}
            tosend['Chunk'] = True
            Connection.Send( tosend , Delay = DELAY)
        else:
            DumpHTTPResponseToLog( Response )
            Connection.Send( Response , Delay = DELAY)

    def keepConnectionAlive( self ):

        self.heartbeats += 1
        Domoticz.Log("%s Connections established" %len(self.httpServerConns))
        for con in self.httpServerConns:
            Domoticz.Log("Connection established: %s" %self.httpServerConns[con].Name)
        return

    def do_rest( self, Connection, verb, data, version, command, parameters):

        REST_COMMANDS = { 
                'device':        {'Name':'device',        'Verbs':{'GET'}, 'function':self.rest_Device},
                'nwk-stat':      {'Name':'nwk_stat',      'Verbs':{'GET','DELETE'}, 'function':self.rest_nwk_stat},
                'permit-to-join':{'Name':'permit-to-join','Verbs':{'GET','PUT'}, 'function':self.rest_PermitToJoin},
                'plugin':        {'Name':'plugin',        'Verbs':{'GET'}, 'function':self.rest_PluginEnv},
                'plugin-stat':   {'Name':'plugin-stat',   'Verbs':{'GET'}, 'function':self.rest_plugin_stat},
                'req-nwk-inter': {'Name':'nwk-nwk-inter', 'Verbs':{'GET'}, 'function':self.rest_req_nwk_inter},
                'req-topologie': {'Name':'req-topologie', 'Verbs':{'GET'}, 'function':self.rest_req_topologie},
                'setting':       {'Name':'setting',       'Verbs':{'GET','PUT'}, 'function':self.rest_Settings},
                'topologie':     {'Name':'topologie',     'Verbs':{'GET','DELETE'}, 'function':self.rest_netTopologie},
                'zdevice':       {'Name':'zdevice',       'Verbs':{'GET'}, 'function':self.rest_zDevice},
                'zdevice-name':  {'Name':'zdevice-name',  'Verbs':{'GET','PUT'}, 'function':self.rest_zDevice_name},
                'zdevice-raw':   {'Name':'zdevice-raw',  'Verbs':{'GET','PUT'}, 'function':self.rest_zDevice_raw},
                'zgroup':        {'Name':'device',        'Verbs':{'GET'}, 'function':self.rest_zGroup},
                'zgroup-list-available-device':   
                                 {'Name':'zgroup-list-available-devic',        'Verbs':{'GET'}, 'function':self.rest_zGroup_lst_avlble_dev},
                'zigate':        {'Name':'zigate',        'Verbs':{'GET'}, 'function':self.rest_zigate}
                }

        Domoticz.Log("do_rest - Verb: %s, Command: %s, Param: %s" %(verb, command, parameters))
        HTTPresponse = {}
        if command in REST_COMMANDS:
            if verb in REST_COMMANDS[command]['Verbs']:
                HTTPresponse = setupHeadersResponse()
                HTTPresponse["Headers"]["Cache-Control"] = "no-store, no-cache"
                HTTPresponse = REST_COMMANDS[command]['function']( verb, data, parameters)

        if HTTPresponse == {}:
            # We reach here due to failure !
            HTTPresponse = setupHeadersResponse()
            HTTPresponse["Status"] = "400 BAD REQUEST"
            HTTPresponse["Data"] = 'Unknown REST command'
            HTTPresponse["Headers"]["Content-Type"] = "text/plain; charset=utf-8"

        self.sendResponse( Connection, HTTPresponse )


    def rest_req_nwk_inter( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
        if verb == 'GET':
            action = {}
            action['Name'] = "Nwk-Interferences"
            action['TimeStamp'] = int(time())
            _response["Data"] = json.dumps( action, sort_keys=False )

            NwkMgtUpdReq( self, ['11','12','13','14','15','16','17','18','19','20','21','22','23','24','25','26'] , mode='scan')

        return _response

    def rest_req_topologie( self, verb, data, parameters):
        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
        if verb == 'GET':
            action = {}
            action['Name'] = 'Req-Topology'
            action['TimeStamp'] = int(time())
            _response["Data"] = json.dumps( action, sort_keys=False )

            # Need to make hook in onHeart to 1) Start the LQI process, 2) continue the scan
        return _response

    def rest_zigate( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
        if verb == 'GET':
            if self.zigatedata:
                _response["Data"] = json.dumps( self.zigatedata, sort_keys=False )
        return _response


    def rest_PluginEnv( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
        if verb == 'GET':
                _response["Data"] = json.dumps( self.pluginparameters, sort_keys=False )
        return _response

    def rest_netTopologie( self, verb, data, parameters):

        _filename = self.pluginconf.pluginConf['pluginReports'] + 'LQI_reports-' + '%02d' %self.hardwareID + '.json'
        Domoticz.Log("Filename: %s" %_filename)

        _response = setupHeadersResponse()
        _response["Data"] = "{}"
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'DELETE':
            os.remove( _filename )
            action = {}
            action['Name'] = 'File-Removed'
            action['FileName'] = _filename
            _response['Data'] = json.dumps( action , sort_keys=True)
            return _response

        if not os.path.isfile( _filename ) :
            _response['Data'] = json.dumps( {} , sort_keys=False ) 
            return _response

        # Read the file, as we have anyway to do it
        _topo = {}           # All Topo reports
        _timestamps_lst = [] # Just the list of Timestamps
        with open( _filename , 'rt') as handle:
            for line in handle:
                if line[0] != '{' and line[-1] != '}': continue
                entry = json.loads( line, encoding=dict )
                for _ts in entry:
                    _timestamps_lst.append( _ts )
                    _topo[_ts] = [] # List of Father -> Child relation for one TimeStamp
                    reportLQI = entry[_ts]
                    for item in reportLQI:
                        if item != '0000' and item not in self.ListOfDevices:
                            continue
                        for x in  reportLQI[item]:
                            # Report only Child relationship
                            if item == x: continue
                            if x != '0000' and x not in self.ListOfDevices: continue
                            if reportLQI[item][x]['_relationshp'] not in ( 'Child', 'Parent' ): continue

                            if reportLQI[item][x]['_relationshp'] == "Child":
                                master = 'Father'
                                slave = 'Child'
                            elif reportLQI[item][x]['_relationshp'] == "Parent":
                                master = 'Child'
                                slave = 'Father'

                            _relation = {}
                            _relation[master] = item
                            _relation[slave] = x
                            _relation["_linkqty"] = int(reportLQI[item][x]['_lnkqty'], 16)
                            _relation["DeviceType"] = reportLQI[item][x]['_devicetype']

                            if item != "0000":
                                if 'ZDeviceName' in self.ListOfDevices[item]:
                                    if self.ListOfDevices[item]['ZDeviceName'] != "" and self.ListOfDevices[item]['ZDeviceName'] != {}:
                                        _relation[master] = self.ListOfDevices[item]['ZDeviceName']
                            if x != "0000":
                                if 'ZDeviceName' in self.ListOfDevices[x]:
                                    if self.ListOfDevices[x]['ZDeviceName'] != "" and self.ListOfDevices[x]['ZDeviceName'] != {}:
                                        _relation[slave] = self.ListOfDevices[x]['ZDeviceName']

                            Domoticz.Log("%10s Relationship - %15.15s - %15.15s %7s %3s %13s %2s" \
                                    %( _ts, _relation['Father'], _relation['Child'], reportLQI[item][x]['_relationshp'], _relation["_linkqty"], _relation["DeviceType"], reportLQI[item][x]['_depth']))

                            _topo[_ts].append( _relation )

        if verb == 'GET':
            if len(parameters) == 0:
                # Send list of Time Stamps
                _response['Data'] = json.dumps( _timestamps_lst , sort_keys=True)

            elif len(parameters) == 1:
                timestamp = parameters[0]
                if timestamp in _topo:
                    _response['Data'] = json.dumps( _topo[timestamp] , sort_keys=True)
                else:
                    _response['Data'] = json.dumps( [] , sort_keys=True)

        return _response

    def rest_nwk_stat( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        _filename = self.pluginconf.pluginConf['pluginReports'] + 'Network_scan-' + '%02d' %self.hardwareID + '.json'

        if verb == 'DELETE':
            os.remove( _filename )
            action = {}
            action['Name'] = 'File-Removed'
            action['FileName'] = _filename
            _response['Data'] = json.dumps( action , sort_keys=True)
            return _response

        _timestamps_lst = [] # Just the list of Timestamps
        _scan = {}
        if os.path.isfile( _filename ) :
            Domoticz.Log("Opening file: %s" %_filename)
            with open( _filename , 'rt') as handle:
                for line in handle:
                    if line[0] != '{' and line[-1] != '}': continue
                    entry = json.loads( line, encoding=dict )
                    for _ts in entry:
                        _timestamps_lst.append( _ts )
                        NetworkScan = entry[_ts]
                        _scan[_ts] = {}
                        for item in NetworkScan:
                            _scan[_ts][item] = NetworkScan[item]
        if verb == 'GET':
            if len(parameters) == 0:
                _response['Data'] = json.dumps( _timestamps_lst , sort_keys=True)

            elif len(parameters) == 1:
                timestamp = parameters[0]
                if timestamp in _scan:
                    _response['Data'] = json.dumps( _scan[timestamp] , sort_keys=True)
                else:
                    _response['Data'] = json.dumps( [] , sort_keys=True)
        return _response

    def rest_plugin_stat( self, verb, data, parameters):

        Statistics = {}
        Statistics['CRC'] =self.statistics._crcErrors
        Statistics['FrameErrors'] =self.statistics._frameErrors
        Statistics['Sent'] =self.statistics._sent
        Statistics['Received'] =self.statistics._received
        Statistics['Cluster'] =self.statistics._clusterOK
        Statistics['ReTx'] =self.statistics._reTx
        Statistics['CurrentLoad'] = len(self.ZigateComm._normalQueue)
        Statistics['MaxLoad'] =self.statistics._MaxLoad
        Statistics['StartTime'] =self.statistics._start

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
        if verb == 'GET':
                _response["Data"] = json.dumps( Statistics, sort_keys=True )
        return _response

    def rest_Settings( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            if len(parameters) == 0:
                setting_lst = []
                for param in self.pluginconf.pluginConf:
                    if not SETTINGS[param]['hidden']:
                        setting = {}
                        setting['Name'] = param
                        setting['default_value'] = SETTINGS[param]['default']
                        setting['DataType'] = SETTINGS[param]['type']
                        setting['restart_need'] = SETTINGS[param]['restart']
                        setting['current_value'] = self.pluginconf.pluginConf[param] 
                        setting_lst.append( setting )
                _response["Data"] = json.dumps( setting_lst, sort_keys=True )

        elif verb == 'PUT':
            _response["Data"] = None
            data = data.decode('utf8')
            Domoticz.Log("Data: %s" %data)
            setting_lst = eval(data)

            for setting in setting_lst:
                Domoticz.Log("setting: %s" %setting)

                for param in setting:
                    if param not in SETTINGS:
                        Domoticz.Error("Unexpectped parameter: %s" %item)
                        Domoticz.Error("Unexpected number of Parameter")
                        _response["Data"] = { 'unexpected parameters %s' %item }
                        _response["Status"] = "400 SYNTAX ERROR"
                        break
                    else:
                        Domoticz.Log("loading %s" %param)
                        if param in self.pluginconf.pluginConf:
                            if self.pluginconf.pluginConf[param] != setting[param]['current_value']:
                                self.pluginconf.pluginConf[param] = setting[param]['current_value']
        return _response

    def rest_PermitToJoin( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            duration = self.permitTojoin['duration']
            timestamp = self.permitTojoin['Starttime']
            info = {}
            if self.permitTojoin['Duration'] == 255:
                info['PermitToJoin'] = 255
            elif int(time()) >= ( self.permitTojoin['Starttime'] + self.permitTojoin['Duration']):
                info['PermitToJoin'] = 0
            else:
                rest =  ( self.permitTojoin['Starttime'] + self.permitTojoin['Duration'] ) - int(time())
                Domoticz.Log("remain %s s" %rest)
                info['PermitToJoin'] = rest

            _response["Data"] = json.dumps( info, sort_keys=False )

        elif verb == 'PUT':
            _response["Data"] = None
            if len(parameters) == 0:
                data = data.decode('utf8')
                data = json.loads(data)
                Domoticz.Log("parameters: %s value = %s" %( 'PermitToJoin', data['PermitToJoin']))
                ZigatePermitToJoin(self, int( data['PermitToJoin']))

        return _response

    def rest_Device( self, verb, data, parameters):

        _dictDevices = {}
        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            if self.Devices is None or len(self.Devices) == 0:
                return _response

            if len(parameters) == 0:
                # Return the Full List of ZIgate Domoticz Widget
                device_lst = []
                for x in self.Devices:
                    device_info = {}
                    device_info['_DeviceID'] = self.Devices[x].DeviceID
                    device_info['Name'] = self.Devices[x].Name
                    device_info['ID'] = self.Devices[x].ID
                    device_info['sValue'] = self.Devices[x].sValue
                    device_info['nValue'] = self.Devices[x].nValue
                    device_info['SignaleLevel'] = self.Devices[x].SignalLevel
                    device_info['BatteryLevel'] = self.Devices[x].BatteryLevel
                    device_info['TimedOut'] = self.Devices[x].TimedOut
                    device_info['Type'] = self.Devices[x].Type
                    device_info['SwitchType'] = self.Devices[x].SwitchType
                    device_lst.append( device_info )
                _response["Data"] = json.dumps( device_lst, sort_keys=False )

            elif len(parameters) == 1:
                for x in self.Devices:
                    if parameters[0] == self.Devices[x].DeviceID:
                        _dictDevices = {}
                        _dictDevices['_DeviceID'] = self.Devices[x].DeviceID
                        _dictDevices['Name'] = self.Devices[x].Name
                        _dictDevices['ID'] = self.Devices[x].ID
                        _dictDevices['sValue'] = self.Devices[x].sValue
                        _dictDevices['nValue'] = self.Devices[x].nValue
                        _dictDevices['SignaleLevel'] = self.Devices[x].SignalLevel
                        _dictDevices['BatteryLevel'] = self.Devices[x].BatteryLevel
                        _dictDevices['TimedOut'] = self.Devices[x].TimedOut
                        _dictDevices['Type'] = self.Devices[x].Type
                        _dictDevices['SwitchType'] = self.Devices[x].SwitchType
                        _response["Data"] = json.dumps( _dictDevices, sort_keys=False )
                        break
            else:
                device_lst = []
                for parm in parameters:
                    device_info = {}
                    for x in self.Devices:
                        if parm == self.Devices[x].DeviceID:
                            device_info = {}
                            device_info['_DeviceID'] = self.Devices[x].DeviceID
                            device_info['Name'] = self.Devices[x].Name
                            device_info['ID'] = self.Devices[x].ID
                            device_info['sValue'] = self.Devices[x].sValue
                            device_info['nValue'] = self.Devices[x].nValue
                            device_info['SignaleLevel'] = self.Devices[x].SignalLevel
                            device_info['BatteryLevel'] = self.Devices[x].BatteryLevel
                            device_info['TimedOut'] = self.Devices[x].TimedOut
                            device_info['Type'] = self.Devices[x].Type
                            device_info['SwitchType'] = self.Devices[x].SwitchType
                            device_lst.append( device_info )
                _response["Data"] = json.dumps( device_lst, sort_keys=False )
        return _response

    def rest_zGroup_lst_avlble_dev( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            device_lst = []
            for x in self.ListOfDevices:
                if x == '0000': 

                    _device = {}
                    _device['_NwkId'] = '0000'
                    _device['WidgetList'] = []

                    _widget = {}
                    _widget['_ID'] =  ''
                    _widget['Name'] =  ''
                    if 'IEEE' in self.ListOfDevices['0000']: _widget['IEEE'] =  self.ListOfDevices['0000']['IEEE'] 
                    else: _widget['IEEE'] =  'Fake for None Mode'
                    _widget['Ep'] =  '01' 
                    _widget['ZDeviceName'] =  'Zigate (Coordinator)'
                    _device['WidgetList'].append( _widget )
                    device_lst.append( _device )
                    continue
                if 'MacCapa' not in self.ListOfDevices[x]:
                    continue
                
                IkeaRemote = False
                if 'Type' in self.ListOfDevices[x]:
                    if self.ListOfDevices[x]['Type'] == 'Ikea_Round_5b':
                        IkeaRemote = True
                if self.ListOfDevices[x]['MacCapa'] != '8e' and not IkeaRemote:
                    continue

                if 'Ep' in self.ListOfDevices[x]:
                    if 'ZDeviceName' in self.ListOfDevices[x] and \
                          'IEEE' in self.ListOfDevices[x]:
                        _device = {}
                        _device['_NwkId'] = x
                        _device['WidgetList'] = []
                        for ep in self.ListOfDevices[x]['Ep']:
                            if 'Type' in self.ListOfDevices[x]:
                                if self.ListOfDevices[x]['Type'] == 'Ikea_Round_5b':
                                    if ep == '01':
                                        if 'ClusterType' in self.ListOfDevices[x]['Ep']['01']:
                                            widgetID = ''
                                            for iterDev in self.ListOfDevices[x]['Ep']['01']['ClusterType']:
                                                if self.ListOfDevices[x]['Ep']['01']['ClusterType'][iterDev] == 'Ikea_Round_5b':
                                                    widgetID = iterDev
                                                    for widget in self.Devices:
                                                        if self.Devices[widget].ID == int(widgetID):
                                                            _widget = {}
                                                            _widget['_ID'] =  self.Devices[widget].ID 
                                                            _widget['Name'] =  self.Devices[widget].Name 
                                                            _widget['IEEE'] =  self.ListOfDevices[x]['IEEE'] 
                                                            _widget['Ep'] =  ep 
                                                            _widget['ZDeviceName'] =  self.ListOfDevices[x]['ZDeviceName'] 
                                                            _device['WidgetList'].append( _widget )
                                                            break
                                                    device_lst.append( _device )
                                            continue # Next Ep
                            if '0004' not in self.ListOfDevices[x]['Ep'][ep] and \
                                'ClusterType' not in self.ListOfDevices[x]['Ep'][ep] and \
                                '0006' not in self.ListOfDevices[x]['Ep'][ep] and \
                                '0008' not in  self.ListOfDevices[x]['Ep'][ep]:
                                continue
                            if 'ClusterType' in self.ListOfDevices[x]['Ep'][ep]:
                                for widgetID in self.ListOfDevices[x]['Ep'][ep]['ClusterType']:
                                    if self.ListOfDevices[x]['Ep'][ep]['ClusterType'][widgetID] not in ( 'LvlControl', 'Switch', 'Plug', 
                                        "SwitchAQ2", "DSwitch", "Button", "DButton", 'LivoloSWL', 'LivoloSWR',
                                        'ColorControlRGB', 'ColorControlWW', 'ColorControlRGBWW', 'ColorControlFull', 'ColorControl',
                                        'WindowCovering'):
                                        continue
                                    for widget in self.Devices:
                                        if self.Devices[widget].ID == int(widgetID):
                                            _widget = {}
                                            _widget['_ID'] =  self.Devices[widget].ID 
                                            _widget['Name'] =  self.Devices[widget].Name 
                                            _widget['IEEE'] =  self.ListOfDevices[x]['IEEE'] 
                                            _widget['Ep'] =  ep 
                                            _widget['ZDeviceName'] =  self.ListOfDevices[x]['ZDeviceName'] 
                                            _device['WidgetList'].append( _widget )

                device_lst.append( _device )
            _response["Data"] = json.dumps( device_lst, sort_keys=False )
            return _response

    def rest_zDevice_name( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"

        if verb == 'GET':
            _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"
            device_lst = []
            for x in self.ListOfDevices:
                if x == '0000': continue
                device = {}
                device['_NwkId'] = x

                for item in ( 'ZDeviceName', 'IEEE', 'Model', 'MacCapa', 'Status', 'Health'):
                    if item in self.ListOfDevices[x]:
                        if item != 'MacCapa':
                            device[item.strip()] = self.ListOfDevices[x][item]
                        else:
                                device['MacCapa'] = []
                                mac_capability = int(self.ListOfDevices[x][item],16)
                                AltPAN      =   ( mac_capability & 0x00000001 )
                                DeviceType  =   ( mac_capability >> 1 ) & 1
                                PowerSource =   ( mac_capability >> 2 ) & 1
                                ReceiveonIdle = ( mac_capability >> 3 ) & 1
                                if DeviceType == 1 :
                                    device['MacCapa'].append("FFD")
                                else :
                                    device['MacCapa'].append("RFD")
                                if ReceiveonIdle == 1 :
                                    device['MacCapa'].append("RxonIdle")
                                if PowerSource == 1 :
                                    device['MacCapa'].append("MainPower")
                                else :
                                    device['MacCapa'].append("Battery")
                                Domoticz.Log("decoded MacCapa from: %s to %s" %(self.ListOfDevices[x][item], str(device['MacCapa'])))
                    else:
                        device[item.strip()] = ""

                device['WidgetList'] = []
                for ep in self.ListOfDevices[x]['Ep']:
                    if 'ClusterType' in self.ListOfDevices[x]['Ep'][ep]:
                        for widgetID in self.ListOfDevices[x]['Ep'][ep]['ClusterType']:
                            for widget in self.Devices:
                                if self.Devices[widget].ID == int(widgetID):
                                    Domoticz.Log("Widget Name: %s %s" %(widgetID, self.Devices[widget].Name))
                                    device['WidgetList'].append( self.Devices[widget].Name )

                device_lst.append( device )
            #_response["Data"] = json.dumps( device_lst, sort_keys=True )
            _response["Data"] = json.dumps( device_lst, sort_keys=False )

        elif verb == 'PUT':
            _response["Data"] = None
            data = data.decode('utf8')
            Domoticz.Log("Data: %s" %data)
            data = eval(data)
            for x in data:
                if 'ZDeviceName' in x and 'IEEE' in x:
                    for dev in self.ListOfDevices:
                        if self.ListOfDevices[dev]['IEEE'] == x['IEEE'] and \
                                self.ListOfDevices[dev]['ZDeviceName'] != x['ZDeviceName']:
                            self.ListOfDevices[dev]['ZDeviceName'] = x['ZDeviceName']
                            Domoticz.Log("Updating ZDeviceName to %s for IEEE: %s NWKID: %s" \
                                    %(self.ListOfDevices[dev]['ZDeviceName'], self.ListOfDevices[dev]['IEEE'], dev))
                else:
                    Domoticz.Error("wrong data received: %s" %data)

        return _response

    def rest_zDevice( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            if self.Devices is None or len(self.Devices) == 0:
                return _response
            if self.ListOfDevices is None or len(self.ListOfDevices) == 0:
                return _response
            if len(parameters) == 0:
                zdev_lst = []
                for item in self.ListOfDevices:
                    device = {}
                    device['_NwkId'] = item
                    # Main Attributes
                    for attribut in ('Health', 'ZDeviceName', 'Status', 'RSSI', 'Model', 'IEEE', 'ProfileID', 'ZDeviceID', 'Manufacturer', 'DeviceType', 'LogicalType', 'PowerSource', 'ReceiveOnIdle', 'App Version', 'Stack Version', 'HW Version' ):

                        if attribut in self.ListOfDevices[item]:
                            device[attribut] = self.ListOfDevices[item][attribut]
                        else:
                            device[attribut] = ''

                    # Last Seen Information
                    device['LastSeen'] = ''
                    if 'Stamp' in self.ListOfDevices[item]:
                        if 'LastSeen' in self.ListOfDevices[item]['Stamp']:
                            device['LastSeen'] = self.ListOfDevices[item]['Stamp']['LastSeen']

                    # ClusterType
                    _widget_lst = []
                    if 'ClusterType' in self.ListOfDevices[item]:
                        for widgetId in self.ListOfDevices[item]['ClusterType']:
                            widget = {}
                            widget['_WidgetID'] = widgetId
                            widget['WidgetName'] = ''
                            for x in self.Devices:
                                if self.Devices[x].ID == int(widgetId):
                                    widget['WidgetName'] = self.Devices[x].Name
                                    break
                            widget['WidgetType'] = self.ListOfDevices[item]['ClusterType'][widgetId]
                            _widget_lst.append( widget )

                    # Ep informations
                    ep_lst = []
                    if 'Ep' in self.ListOfDevices[item]:
                        for epId in self.ListOfDevices[item]['Ep']:
                            _ep = {}
                            _ep['Ep'] = epId
                            _ep['ClusterList'] = []
                            for cluster in self.ListOfDevices[item]['Ep'][epId]:
                                if cluster == 'ColorMode': continue
                                if cluster == 'Type':
                                    device['Type'] = self.ListOfDevices[item]['Ep'][epId]['Type']
                                    continue
                                if cluster == 'ClusterType':
                                    for widgetId in self.ListOfDevices[item]['Ep'][epId]['ClusterType']:
                                        widget = {}
                                        widget['_WidgetID'] = widgetId
                                        widget['WidgetName'] = ''
                                        for x in self.Devices:
                                            if self.Devices[x].ID == int(widgetId):
                                                widget['WidgetName'] = self.Devices[x].Name
                                                break
                                        widget['WidgetType'] = self.ListOfDevices[item]['Ep'][epId]['ClusterType'][widgetId]
                                        _widget_lst.append( widget )
                                    continue
                                _cluster = {}
                                if cluster in ZCL_CLUSTERS_LIST:
                                    _cluster[cluster] = ZCL_CLUSTERS_LIST[cluster]
                                else:
                                    _cluster[cluster] = "Unknown"
                                _ep['ClusterList'].append( _cluster )

                            ep_lst.append ( _ep )
                    device['Ep'] = ep_lst
                    device['WidgetList'] = _widget_lst

                    # Last Commands
                    lastcmd_lst = []
                    if 'Last Cmds' in self.ListOfDevices[item]:
                        for timestamp, cmd in self.ListOfDevices[item]['Last Cmds']:
                            _cmd = {}
                            _cmd['CmdCode'] = cmd
                            _cmd['TimeStamps'] =  timestamp
                            lastcmd_lst.append( _cmd )
                    device['LastCmds'] = lastcmd_lst
                    zdev_lst.append( device )

                _response["Data"] = json.dumps( zdev_lst, sort_keys=False )
        return _response


    def rest_zDevice_raw( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        if verb == 'GET':
            if self.Devices is None or len(self.Devices) == 0:
                return _response
            if self.ListOfDevices is None or len(self.ListOfDevices) == 0:
                return _response
            if len(parameters) == 0:
                zdev_lst = []
                for item in self.ListOfDevices:
                    zdev_lst.append(self.ListOfDevices[item])
                _response["Data"] = json.dumps( zdev_lst, sort_keys=False )
            elif len(parameters) == 1:
                if parameters[0] in self.ListOfDevices:
                    _response["Data"] =  json.dumps( self.ListOfDevices[parameters[0]], sort_keys=False ) 
                elif parameters[0] in self.IEEE2NWK:
                    _response["Data"] =  json.dumps( self.ListOfDevices[self.IEEE2NWK[parameters[0]]], sort_keys=False ) 

        return _response

    def rest_zGroup( self, verb, data, parameters):

        _response = setupHeadersResponse()
        _response["Data"] = {}
        _response["Status"] = "200 OK"
        _response["Headers"]["Content-Type"] = "application/json; charset=utf-8"

        Domoticz.Log("rest_zGroup - ListOfGroups = %s" %str(self.groupmgt))
        if verb == 'GET':
            if self.groupmgt is None:
                return _response
            ListOfGroups = self.groupmgt.ListOfGroups
            if ListOfGroups is None or len(ListOfGroups) == 0:
                return _response

            if len(parameters) == 0:
                zgroup_lst = []
                for item in ListOfGroups:
                    Domoticz.Log("Process Group: %s" %item)
                    zgroup = {}
                    zgroup['_GroupId'] = item
                    zgroup['GroupName'] = ListOfGroups[item]['Name']
                    zgroup['Devices'] = []
                    for dev, ep in ListOfGroups[item]['Devices']:
                        Domoticz.Log("--> add %s %s" %(dev, ep))
                        _dev = {}
                        _dev['_NwkId'] = dev
                        _dev['Ep'] = ep
                        zgroup['Devices'].append( _dev )
                    zgroup_lst.append(zgroup)
                _response["Data"] = json.dumps( zgroup_lst, sort_keys=False )

            elif len(parameters) == 1:
                if parameters[0] in ListOfGroups:
                    item =  parameters[0]
                    zgroup = {}
                    zgroup['_GroupId'] = item
                    zgroup['GroupName'] = ListOfGroups[item]['Name']
                    zgroup['Devices'] = {}
                    for dev, ep in ListOfGroups[item]['Devices']:
                        Domoticz.Log("--> add %s %s" %(dev, ep))
                        zgroup['Devices'][dev] = ep 
                    _response["Data"] = json.dumps( zgroup, sort_keys=False )

        return _response


def DumpHTTPResponseToLog(httpDict):

    if not DEBUG_HTTP:
        return
    if isinstance(httpDict, dict):
        Domoticz.Log("HTTP Details ("+str(len(httpDict))+"):")
        for x in httpDict:
            if isinstance(httpDict[x], dict):
                Domoticz.Log("--->'"+x+" ("+str(len(httpDict[x]))+"):")
                for y in httpDict[x]:
                    Domoticz.Log("------->'" + y + "':'" + str(httpDict[x][y]) + "'")
            else:
                if x == 'Data':
                    Domoticz.Log("--->'%s':'%.40s'" %(x, str(httpDict[x])))
                else:
                    Domoticz.Log("--->'" + x + "':'" + str(httpDict[x]) + "'")

def setupHeadersResponse():

    _response = {}
    _response["Headers"] = {}
    _response["Headers"]["Connection"] = "Keep-alive"
    _response["Headers"]["User-Agent"] = "Plugin-Zigate/v1"
    _response["Headers"]["Server"] = "Domoticz"
    #_response["Headers"]["Cache-Control"] = "no-cache, no-store, must-revalidate"
    #_response["Headers"]["Pragma"] = "no-cache"
    #_response["Headers"]["Expires"] = "0"
    _response["Headers"]["Accept"] = "*/*"
    #_response["Headers"]["Accept-Ranges"] = "bytes"
    # allow users of a web application to include images from any origin in their own conten
    # and all scripts only to a specific server that hosts trusted code.
    #_response["Headers"]["Content-Security-Policy"] = "default-src 'self'; img-src *"
    #_response["Headers"]["Content-Security-Policy"] = "default-src * 'unsafe-inline' 'unsafe-eval'"
    return _response