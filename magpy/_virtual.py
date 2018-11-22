# -*- coding: utf-8 -*-
"""
Created on Thu Jan 07 2016
Last Modified on Thu Nov 22 2018

Code relating to creating virtual 200^2, BiStim^2, and Rapid^2 Magstim TMS units

@author: Nicolas McNair
"""

from __future__ import division
from multiprocessing import Pipe
from threading import Thread
from sys import version_info, platform
from os.path import realpath, join, dirname
from os import getcwd
from magstim import calcCRC, MagstimError
from collections import OrderedDict
from math import ceil, floor
from threading import Timer
from yaml import load
from ast import literal_eval

#switch timer based on platform
if platform == 'win32':
    # On Windows, use time.clock
    from time import clock
    default_timer = clock
else:
    # On other platforms use time.time
    from time import time    
    default_timer = time

class virtualMagstim(Thread):
    def __init__(self,serialConnection):
        Thread.__init__(self)
        self._magstimConn = serialConnection
        self._instrStatus = OrderedDict([('remoteStatus',0),
                                         ('errorType'   ,0),
                                         ('errorPresent',0),
                                         ('replaceCoil' ,0),
                                         ('coilPresent' ,1),
                                         ('ready'       ,0),
                                         ('armed'       ,0),
                                         ('standby'     ,1)])

        self._params = {'power' : 30}
        self._coilTemp = [240,240] # Expressed in tenths of degrees centigrade
        self._lastFired = float('-Inf')
        self._timeArmed = float('-Inf')
        self._lastCommand = float('-Inf')
        self._connectionTimer = None

    def _startTimer(self):
        self._connectionTimer = Timer(1,self._disconnect)
        self._connectionTimer.start()

    def _getParams(self):
        return str(self._params['power']).zfill(3) + '000000'

    def _getCoilTemp(self):
        return str(self._coilTemp[0]).zfill(3) + str(self._coilTemp[1]).zfill(3)

    def _parseStatus(self,currentStatus):
        status = 0
        for bit in [x for x in currentStatus.values()]:
            status = (status << 1) | bit
        return chr(status)

    def _disconnect(self):
        self._disarm()
        self._instrStatus['remoteStatus'] = 0

    def _disarm(self):
        self._instrStatus['ready'] = 0
        self._instrStatus['armed'] = 0
        self._instrStatus['standby'] = 1

    def _okToFire(self):
        if  0 <= self._params['power'] <= 49:
            return default_timer() > (self._lastFired + 2)
        elif 50 <= self._params['power'] <= 79:
            return default_timer() > (self._lastFired + 3)
        else:
            return default_timer() > (self._lastFired + 4)

    def _processMessage(self,message):
        # N.B. Messages with no data value use '@' as a placeholder - the Magstim doesn't inspect this value however, so can be any ascii character
        # If we're armed or ready to fire and it's been more than a minute since arming or firing then disarm
        if (self._instrStatus['armed'] or self._instrStatus['ready']) and (default_timer() > (max(self._timeArmed, self._lastFired) + 60)):
            self._disarm()
        # If we're currently armed and it's been more than a second since we armed, and we haven't fired too recently, switch status to ready before processing
        if self._instrStatus['armed'] and (default_timer() > (self._timeArmed + 1)) and self._okToFire():
            self._instrStatus['armed'] = 0
            self._instrStatus['ready'] = 1
        # Check CRC of command
        if calcCRC(message[:-1]) != message[-1]:
            messageData = '?'
        else:
            #N.B. Remote control changes are automatically reflected in instrument status, unlike other commands (see below)           
            if message[0] == 'Q':
                self._instrStatus['remoteStatus'] = 1
                messageData = self._parseStatus(self._instrStatus)
            elif message[0] == 'R':
                self._instrStatus['remoteStatus'] = 0
                messageData = self._parseStatus(self._instrStatus)
                self._disconnect()
                if self._connectionTimer is not None:
                    self._connectionTimer.cancel()
            else:
                # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                messageData = self._parseStatus(self._instrStatus)
                if message[0] in {'J', 'F', '@', 'E'}:
                    # Get parameters
                    if message[0] == 'J':
                        messageData += self._getParams()
                    # Get coil temperature
                    elif message[0] == 'F':
                        messageData += self._getCoilTemp()
                    # Disarm
                    elif messageData[0:2] == 'EA':
                        self._disarm()
                    # All other commands require remote control to have been established
                    elif self._instrStatus['remoteStatus']:
                        # Set power
                        if message[0] == '@':
                            try:
                                newParameter = int(message[1:-1])
                            except ValueError:
                                messageData = '?'
                            else:
                                if 0 <= newParameter <= 100:
                                    self._params['power'] = newParameter
                                else:
                                    messageData = 'S'
                        # Set instrument status (arm/fire)
                        elif message[0] == 'E':
                            if message[1] == 'B' and self._instrStatus['standby']:
                                self._instrStatus['armed'] = 1
                                self._instrStatus['standby'] = 0
                                self._timeArmed = default_timer()
                                self._startTimer()
                            elif message[1] == 'H' and self._instrStatus['ready']:
                                self._instrStatus['armed'] = 1
                                self._instrStatus['ready'] = 0
                                self._lastFired = default_timer()
                            else:
                                messageData = 'S'
                    else:
                        messageData = 'S'
                else:
                    return '?'
        # Only reset timer if a valid command is being returned
        if messageData not in {'?','S'} and (self._instrStatus['ready'] or self._instrStatus['armed']):
            if self._connectionTimer is not None:
                self._connectionTimer.cancel()
            self._startTimer()
        returnMessage = message[0] + messageData
        return returnMessage + calcCRC(returnMessage)

    def run(self):
        while True:
            # Check virtual port connection for messages
            message = self._magstimConn.recv()
            # Check if message is signal to shutdown
            if message is not None:
                self._magstimConn.send(self._processMessage(message))
            else:
                break
        self._magstimConn.close()

class virtualBiStim(virtualMagstim):
    def __init__(self,serialConnection):
        super(virtualBiStim,self).__init__(serialConnection)
        self._highResolutionMode = False
        self._params = {'power'    : 30,
                        'powerB'   : 30,
                        'hrMode'   : 0,
                        'ppOffset' : 10}

    def _okToFire(self):
        return default_timer() > (self._lastFired + 4)

    def _getParams(self):
        return str(self._params['power']).zfill(3) + str(self._params['powerB']).zfill(3) + str(self._params['ppOffset']).zfill(3)
    
    def _processMessage(self,message):
        # Try and process message using parent function
        parentParsedMessage = super(virtualBiStim,self)._processMessage(message)
        # If parent returns ?, then it didn't understand the message - so try and parse it here
        if parentParsedMessage[0] == '?':
            if message[0] in {'A','Y','Z','C'}:
                if self._instrStatus['remoteStatus']:
                    # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                    messageData = self._parseStatus(self._instrStatus)
                    if message[0] == 'A':
                        try:
                            newParameter = int(message[1:-1])
                        except ValueError:
                            messageData = '?'
                        else:
                            if 0 <= newParameter <= 100:
                                self._params['powerB'] = newParameter
                            else:
                                messageData = 'S'
                    elif message[0] == 'Y':
                        self._highResolutionMode = True
                    elif message[0] == 'Z':
                        self._highResolutionMode = False
                    elif message[0] == 'C':
                        try:
                            newParameter = int(message[1:-1])
                        except ValueError:
                            messageData = '?'
                        else:
                            if newParameter == 0:
                                if self._instrStatus['standby']:
                                    self._params['ppOffset'] = newParameter
                                else:
                                    messageData = 'S'
                            if 1 <= newParameter <= 999:
                                self._params['ppOffset'] = newParameter
                            else:
                                messageData = 'S'
                else:
                    messageData = 'S'   
            else:
                return '?'
            # Only reset timer if a valid command is being returned
            if messageData not in {'?','S'} and (self._instrStatus['ready'] or self._instrStatus['armed']):
                if self._connectionTimer is not None:
                    self._connectionTimer.cancel()
                self._startTimer()
            returnMessage = message[0] + messageData
            return returnMessage + calcCRC(returnMessage)
        # Otherwise, it did understand the message (one way or another, so return)
        else:
            return parentParsedMessage

class virtualRapid(virtualMagstim):

    # Load settings file (resort to default values if not found)
    __location__ = realpath(join(getcwd(), dirname(__file__)))
    try:
        with open(join(__location__, 'rapid_config.yaml')) as yaml_file:
            config_data = load(yaml_file)
    except:
        DEFAULT_RAPID_TYPE = 0
        DEFAULT_VOLTAGE = 240
        DEFAULT_UNLOCK_CODE = ''
        ENFORCE_ENERGY_SAFETY = True
        DEFAULT_VIRTUAL_VERSION = (5,0,0)
        DEFAULT_UNLOCK_CODE = '1234-12345678-ý\x91'
    else:
        DEFAULT_RAPID_TYPE = config_data['defaultRapidType']
        DEFAULT_VOLTAGE = config_data['defaultVoltage']
        DEFAULT_UNLOCK_CODE = config_data['unlockCode']
        ENFORCE_ENERGY_SAFETY = config_data['enforceEnergySafety']
        DEFAULT_VIRTUAL_VERSION = literal_eval(config_data['virtualVersionNumber'])
        DEFAULT_UNLOCK_CODE = config_data['virtualUnlockCode']

    # Load system info file
    with open(join(__location__, 'rapid_system_info.yaml')) as yaml_file:
        system_info = load(yaml_file)
    # Maximum allowed rTMS frequency based on voltage and current power setting
    MAX_FREQUENCY = system_info['maxFrequency']
    # Minimum wait time (s) required for rTMS train. Power:Joules per pulse
    JOULES = system_info['joules']

    def getRapidMaxOnTime(power, frequency):
        """ Calculate maximum train duration for given power and frequency. If greater than 60 seconds, will allow for continuous operation for up to 6000 pulses."""
        return 63000.0 / (frequency * virtualRapid.JOULES[power])

    def __init__(self,serialConnection, superRapid=DEFAULT_RAPID_TYPE, unlockCode=DEFAULT_UNLOCK_CODE, voltage=DEFAULT_VOLTAGE, version=DEFAULT_VIRTUAL_VERSION):
        super(virtualRapid,self).__init__(serialConnection)

        self._super = superRapid
        self._unlockCode = unlockCode
        self._voltage = voltage
        self._version = version
        self._secretUnlockCode = '1234-12345678-ý\x91'
        # If an unlock code has been supplied, then the Rapid requires a different command to stay in contact with it.
        if self._unlockCode:
            self._connectionCommandCharacter = 'x'
        else:
            self._connectionCommandCharacter = 'Q'

        self._rapidStatus = OrderedDict([('modifiedCoilAlgorithm', 0),
                                         ('thetaPSUDetected',      1),
                                         ('coilReady',             1),
                                         ('hvpsuConnected',        1),
                                         ('singlePulseMode',       1),
                                         ('wait',                  0),
                                         ('train',                 0),
                                         ('enhancedPowerMode',     0)])

        self._extendedStatus = {'LSB': OrderedDict([('plus1ModuleDetected',     (1 if self._super > 1 else 0)),
                                                    ('specialTriggerModeActive', 0),
                                                    ('chargeDelaySet',           0),
                                                    ('Unused3',                  0),
                                                    ('Unused4',                  0),
                                                    ('Unused5',                  0),
                                                    ('Unused6',                  0),
                                                    ('Unused7',                  0)]),
                                'MSB' :{'Unused' + str(x):0 for x in range(8,16)}}

        self._params = {'power'     : 30,
                        'frequency' : 0,
                        'nPulses'   : 1,
                        'duration'  : 0,
                        'wait'      : 1}

        self._chargeDelay = 0

    def _okToFire(self):
        return default_timer() > (self._lastFired + self._params['wait'])

    def _getParams(self):
        return str(self._params['power']).zfill(3) + str(self._params['frequency']).zfill(4) + str(self._params['nPulses']).zfill(4) + str(self._params['duration']).zfill(3) + str(self._params['wait']).zfill(3)

    def _getMaxFreq(self):
        return virtualRapid.MAX_FREQUENCY[self._voltage][self._super][self._params['power']]

    def _processMessage(self,message):

        # Later versions of Magstim only respond to commands that don't require remote control when not under remote control
        if (message[0] not in {'Q', 'R', 'F', '\\'}) and not self._instrStatus['remoteStatus']:
            return None
        # Catch overloaded Magstim commands here
        if message[0] in {'Q', '@', 'J'}:
            parentParsedMessage = '?'
        # Otherwise, try and process message using parent function
        else:
            parentParsedMessage = super(virtualRapid,self)._processMessage(message)
        # If parent returns ?, then it didn't understand the message - so try and parse it here
        if parentParsedMessage == '?':
            if message[0] in {'Q','\\','I','N','@','E','b','^','_','B','D','['} or (self._version > (9,0,0) and message[0] in {'x','o','n'}):
                # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                messageData = self._parseStatus(self._instrStatus)
                # Overwrite enable remote control
                if message[0] == 'Q':
                    if self._version >= (9,0,0) and message[1:-1] != self._secretUnlockCode:
                        return None
                    else:
                        self._instrStatus['remoteStatus'] = 1
                        messageData = self._parseStatus(self._instrStatus)
                # Return Rapid parameters
                elif message[0] == '\\':
                    messageData += self._parseStatus(self._rapidStatus)
                    messageData += self._getParams()
                # Return Error Code (Not currently implemented - Not sure if this needs remote control or not)
                elif message[0] == 'I':
                    messageData = 'S'
                # All other commands require remote control to have been established
                elif self._instrStatus['remoteStatus']:
                    # Get device version (for some reason this needs remote control status)
                    if message[0] == 'N':
                        if message[1] == 'D':
                            messageData = ''.join([str(x) for x in self._version]) + '\x00'
                        else:
                            messageData = '?'
                    # Set power
                    elif message[0] == '@':
                        try:
                            newParameter = int(message[1:-1])
                        except ValueError:
                            messageData = '?'
                        else:
                            if 0 <= newParameter <= (110 if self._rapidStatus['enhancedPowerMode'] else 100):
                                self._params['power'] = newParameter
                            else:
                                messageData = 'S'
                    # Get system status
                    elif message[0] == 'x':
                        messageData += self._parseStatus(self._rapidStatus)
                        messageData += self._parseStatus(self._extendedStatus['MSB'])
                        messageData += self._parseStatus(self._extendedStatus['LSB'])
                    # Get charge delay
                    elif message[0] == 'o':
                        messageData += (self._chargeDelay).zfill(4 if self._version >= (10,0,0) else 3)
                    # Set charge delay
                    elif message[0] == 'n':
                        try:
                            newParameter = int(message[1:-1])
                        except ValueError:
                            messageData = '?'
                        else:
                            if 0 <= newParameter <= (10000 if self._version >= (10,0,0) else 2000):
                                self._chargeDelay = newParameter
                            else:
                                messageData = 'S'
                    # Ignoring coil safety switch, so just pass
                    elif message[0] == 'b':
                        pass
                    # Enable/Disable enhanced power mode
                    elif message[0] in {'^','_'}:
                        if self._instrStatus['standby']:
                            messageData += self._getRapidStatus 
                            self._params['enhancedPowerMode'] = 1 if message[0] == '^' else 0
                        else:
                            messageData = 'S'
                    # Toggle repetitive mode
                    elif message[0] == '[' and self._params['singlePulseMode']:
                        if int(message[1:-1]) == 1 and self._instrStatus['standby']:
                            self._params['singlePulseMode'] = 0
                            messageData += self._getRapidStatus
                        else:
                            messageData = 'S'
                    # Set rTMS parameters
                    elif message[0] in {'B','D','['} and not self._params['singlePulseMode']:
                        try:
                            newParameter = int(message[1:-1])
                        except ValueError:
                            messageData = '?'
                        else:
                            if message[0] == 'B':
                                if 0 < newParameter < self._getMaxFreq():
                                    messageData += self._getRapidStatus 
                                    self._params['frequency'] = newParameter
                                else:
                                    messageData = 'S'
                            elif message[0] == 'D':
                                if 1<= newParameter <= 6000:
                                    messageData += self._getRapidStatus 
                                    self._params['nPulses'] = newParameter
                                else:
                                    messageData = 'S'
                            elif message[0] == '[':
                                if 1 <= newParameter <= (9999 if self._version >= (9,0,0) else 999):
                                    messageData += self._getRapidStatus 
                                    self._params['duration'] = newParameter
                                elif newParameter == 0 and self._instrStatus['standby']:
                                    self._params['singlePulseMode'] = 1
                                    messageData += self._getRapidStatus
                                else:
                                    messageData = 'S'
                else:
                    messageData = 'S'
            else:
                return '?'
            # Only reset timer if a valid command is being returned
            if messageData not in {'?','S'} and (self._instrStatus['ready'] or self._instrStatus['armed']):
                if self._connectionTimer is not None:
                    self._connectionTimer.cancel()
                self._startTimer()
            returnMessage = message[0] + messageData
            return returnMessage + calcCRC(returnMessage)
        # Otherwise, it did understand the message (one way or another, so return)
        else:
            return parentParsedMessage

class virtualPortController(Thread):
    """
    The class creates a Python thread which simulates control over a serial port. Commands for relaying via the serial port are received from separate Python threads/processes via Queues.
    
    N.B. To start the thread you must call start() from the parent Python thread.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for receiving commands to be written to the virtual Magstim unit via the serial port
    serialReadQueue (multiprocessing.Queue): a Queue for returning automated replies from the virtual Magstim unit when requested
    """
    def __init__(self, magstimType, serialWriteQueue, serialReadQueue, **kwargs):
        Thread.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._serialReadQueue = serialReadQueue
        self._portConn, self._magstimConn = Pipe()
        if magstimType == 'Magstim':
            self._magstim = virtualMagstim(self._magstimConn)
        elif magstimType == 'BiStim':
            self._magstim = virtualBiStim(self._magstimConn)
        elif magstimType == 'Rapid':
            self._magstim = virtualRapid(self._magstimConn, **kwargs)
        else:
            raise MagstimError('Unrecognised Magstim type.')
        self._magstim.daemon = True

    def run(self):
        """
        Continuously monitor the serialWriteQueue for commands from other Python threads/processes to be sent to the virtual Magstim.
        
        When requested, will return the automated reply from the virtual Magstim unit to the calling thread via the serialReadQueue.
        
        N.B. This should be called via start() from the parent Python thread.
        """
        #Start up virtual magstim
        self._magstim.start()
                
        #This continually monitors the serialWriteQueue for write requests
        while True:
            message,reply,readBytes = self._serialWriteQueue.get()
            #If the first part of the message is None this signals the thread to close the port and stop
            if message is None:
                self._portConn.send(None)
                break
            #If the first part of the message is a 1 or -1 this signals the thread to do something with the RTS pin, which we don't have - so pass
            elif message in {1,-1}:
                pass
            #Otherwise, the message is a command string
            else:
                #Try writing to the virtual port
                self._portConn.send(message)
                #Get reply
                if self._portConn.poll(0.3):
                    #If we want a reply, read the response from the Magstim and place it in the serialReadQueue
                    if reply:
                        self._serialReadQueue.put([0,self._portConn.recv()])
                    #Otherwise just get rid of the reply from the pipe
                    else:
                        self._portConn.recv()
                else:
                    self._serialReadQueue.put([2,'Timed out while waiting for response.'])
        #If we get here, it's time to shutdown the serial port controller
        self._portConn.close()
        return