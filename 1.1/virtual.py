from __future__ import division
from multiprocessing import Pipe
from threading import Thread
from sys import version_info, platform
from misc import calcCRC
from collections import OrderedDict
from math import ceil, floor
from threading import Timer
from Queue import Empty

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
        self._armedTimer = None

    def _startTimer(self):
        self._armedTimer = Timer(1,self._disconnect)
        self._armedTimer.start()

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
        print('disconnecting')
        self._disarm()
        self._instrStatus['remoteStatus'] = 0

    def _disarm(self):
        self._instrStatus['ready'] = 0
        self._instrStatus['armed'] = 0
        self._instrStatus['standby'] = 1

    def _okToFire(self):
        if (default_timer() < (self._timeArmed + 1)):
            return False
        elif 0 <= self._params['power'] <= 49:
            return default_timer() > (self._lastFired + 2)
        elif 50<= self._params['power'] <= 79:
            return default_timer() > (self._lastFired + 3)
        else:
            return default_timer() > (self._lastFired + 4)

    def _processMessage(self,message):
        # N.B. Messages with no data value use '@' as a placeholder - the Magstim doesn't inspect this value however, so can be any ascii character
        # If we're currently armed and it's been more than a second since we armed, and we haven't fired too recently, switch status to ready
        if self._instrStatus['armed'] and self._okToFire():
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
                if self._armedTimer is not None:
                    self._armedTimer.cancel()
            else:
                # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                messageData = self._parseStatus(self._instrStatus)
                if message[0] in {'J','F','@','E'}:
                    if message[0] == 'J':
                        messageData += self._getParams()
                    elif message[0] == 'F':
                        messageData += self._getCoilTemp()
                    elif message[0] == '@':
                        newParameter = int(message[1:-1])
                        if self._instrStatus['remoteStatus'] and (0<= newParameter <= 100):
                            self._params['power'] = newParameter
                        else:
                            messageData = 'S'
                    elif message[0] == 'E':
                        if message[1] == 'A':
                            self._disarm()
                        elif self._instrStatus['remoteStatus']:
                            if message[1] == 'B' and not (self._instrStatus['armed'] or self._instrStatus['ready']):
                                self._instrStatus['armed'] = 1
                                self._instrStatus['standby'] = 0
                                self._timeArmed = default_timer()
                                self._startTimer()
                            elif message[1] == 'H':
                                if self._instrStatus['ready']:
                                    self._instrStatus['armed'] = 1
                                    self._instrStatus['ready'] = 0
                                    self._lastFired = default_timer()
                                else:
                                    messageData = 'S'
                            else:
                                messageData = 'S'
                        else:
                            messageData = 'S'
                else:
                    return '?'
        # Only reset timer if a valid command is being returned
        if messageData not in {'?','S'} and (self._instrStatus['ready'] or self._instrStatus['armed']):
            if self._armedTimer is not None:
                self._armedTimer.cancel()
            self._startTimer()
        returnMessage = message[0] + messageData
        return returnMessage + calcCRC(returnMessage)

    def run(self):
        while True:
            # Wait until there's something to read
            while not self._magstimConn.poll():
                pass
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
                # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                messageData = self._parseStatus(self._instrStatus)
                if self._instrStatus['remoteStatus']:
                    if message[0] == 'A':
                        newParameter = int(message[1:-1])
                        if self._instrStatus['remoteStatus'] and 0 <= newParameter <= 100:
                            self._params['powerB'] = newParameter
                        else:
                            messageData = 'S'
                    elif message[0] == 'Y':
                        self._biStimParams['hrMode'] = 1
                    elif message[0] == 'Z':
                        self._biStimParams['hrMode'] = 0
                    elif message[0] == 'C':
                        newParameter = int(message[1:-1])
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
                if self._armedTimer is not None:
                    self._armedTimer.cancel()
                self._startTimer()
            returnMessage = message[0] + messageData
            return returnMessage + calcCRC(returnMessage)
        # Otherwise, it did understand the message (one way or another, so return)
        else:
            return parentParsedMessage

class virtualRapid(virtualMagstim):
    def __init__(self,serialConnection,superRapid):
        super(virtualRapid,self).__init__(serialConnection)

        self._super = superRapid

        self._rapidStatus = OrderedDict([('modifiedCoilAlgorithm',0),
                                         ('UNUSED'               ,1),
                                         ('coilReady'            ,1),
                                         ('hvpsuConnected'       ,1),
                                         ('singlePulseMode'      ,1),
                                         ('wait'                 ,1),
                                         ('train'                ,0),
                                         ('enhancedPowerMode'    ,0)]) # CHECK THESE

        self._params = {'power'     : 30,
                        'frequency' : 100,
                        'nPulses'   : 20,
                        'duration'  : 20,
                        'wait'      : 10}

    def _okToFire(self):
        return default_timer() > (self._lastFired + 1)

    def _getParams(self):
        return str(self._params['power']).zfill(3) + str(self._params['frequency']).zfill(4) + str(self._params['nPulses']).zfill(4) + str(self._params['duration']).zfill(3) + str(self._params['wait']).zfill(3)

    def _processMessage(self,message):
        # Catch Magstim and BiStim parameter command here, before passing the message up to parent
        if message[0] in {'R','@'} or message[:2] == 'EH':
            parentParsedMessage = '?'
        # Otherwise, try and process message using parent function
        else:
            parentParsedMessage = super(virtualBiStim,self)._processMessage(message)
        # If parent returns ?, then it didn't understand the message - so try and parse it here
        if parentParsedMessage[0] == '?':
            if message[0] in {'\\','@','E','b','^','_','B','D','['}:
                # Get the instrument status prior to effecting changes (this is Magstim behaviour)
                messageData = self._parseStatus(self._instrStatus)
                if message[0] == 'E':
                    if self._instrStatus['ready']:
                        self._instrStatus['armed'] = 1
                        self._instrStatus['ready'] = 0
                        self._lastFired = default_timer()
                    else:
                        messageData = 'S'
                elif message[0] == '\\':
                    messageData += self._parseStatus(self._rapidStatus)
                    messageData += self._getParams()
                elif message[0] == '@':
                    newParameter = int(message[1:-1])
                    if self._instrStatus['remoteStatus'] and ((0<= newParameter <= 100) or (self._instrStatus['enhancedPowerMode'] and (101<= newParameter <= 110))):
                        self._params['power'] = newParameter
                        #Need to adjust frequency based on power setting; this is relatively easy for a Super Rapid
                        if self._super:
                            if 30 < self._params['power'] < 50:
                                self._params['frequency'] = min(self._params['frequency'], 10 * ceil(100 - (2.5 * (self._params['power'] - 30))))
                            elif self._params['power'] <= 100:
                                self._params['frequency'] = min(self._params['frequency'], 10 * ceil(50 - (0.5 * (self._params['power'] - 50))))
                            else:
                                self._params['frequency'] = 250
                        #...but gets pretty complex for a Standard Rapid for some reason
                        else:
                            if 30 < self._params['power'] < 38:
                                self._params['frequency'] = min(self._params['frequency'], 10 * floor(46 - (1.2 * (self._params['power'] - 31))))
                            elif self._params['power'] < 43:
                                self._params['frequency'] = min(self._params['frequency'], 10 * floor(37 - ((4/3) * (self._params['power'] - 40))))
                            elif self._params['power'] < 47:
                                self._params['frequency'] = min(self._params['frequency'], 330)
                            elif self._params['power'] < 50:
                                self._params['frequency'] = min(self._params['frequency'], 10 * ceil(30 - (self._params['power'] - 50)))
                            elif self._params['power'] < 70:
                                self._params['frequency'] = min(self._params['frequency'], 10 * int(30 - (0.5 * (self._params['power'] - 50))))
                            elif self._params['power'] <= 100:
                                self._params['frequency'] = min(self._params['frequency'], 10 * int(20 - ((1/6) * (self._params['power'] - 70))))
                        self._params['nPulses'] = int((self._params['frequency'] / 10 ) * (self._params['duration'] / 10))  
                    else:
                        messageData = 'S'
                elif message[0] == 'b':
                    pass # Ignoring coil safety switch, so just pass
                elif message[0] == '^':
                        self._params['enhancedPowerMode'] = 1
                        messageData += self._getRapidStatus
                elif message[0] == '_':
                        self._params['enhancedPowerMode'] = 0
                        self._params['power'] = min(self._params['power'], 100)
                        messageData += self._getRapidStatus
                elif message[0] == '[' and self._params['singlePulseMode']:
                    if int(message[1:-1]) == 1:
                        self._params['singlePulseMode'] = 0
                        messageData += self._getRapidStatus
                    else:
                        messageData = 'S'
                elif message[0] in {'B','D','['} and not self._params['singlePulseMode']:
                    if message[0] == 'B':
                        newParameter = int(message[1:-1])
                        if (self._super and (1<= newParameter <= 1000)) or (1<= newParameter <= 500):
                            self._params['frequency'] = newParameter
                            self._params['nPulses'] = int((self._params['frequency'] / 10 ) * (self._params['duration'] / 10))
                            messageData += self._getRapidStatus 
                        else:
                           messageData = 'S'
                    elif message[0] == 'D':
                        newParameter = int(message[1:-1])
                        if 1<= newParameter <= 1000:
                            self._params['nPulses'] = newParameter
                            self._params['duration'] = 10 * (self._params['nPulses'] / (self._params['frequency'] / 10))
                            messageData += self._getRapidStatus
                        else:
                            messageData = 'S'
                    elif message[0] == '[':
                        newParameter = int(message[1:-1])
                        messageData += self._getRapidStatus
                        if 1<= newParameter <= 100:
                            self._params['duration'] = newParameter
                            self._params['nPulses'] = int((self._params['frequency'] / 10 ) * (self._params['duration'] / 10))
                        elif newParameter == 0:
                            self._params['singlePulseMode'] = 1
                        else:
                            messageData = 'S'
            else:
                return '?'
            # Only reset timer if a valid command is being returned
            if messageData not in {'?','S'} and (self._instrStatus['ready'] or self._instrStatus['armed']):
                if self._armedTimer is not None:
                    self._armedTimer.cancel()
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
    def __init__(self,magstimType,serialWriteQueue,serialReadQueue):
        Thread.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._serialReadQueue = serialReadQueue
        self._portConn, self._magstimConn = Pipe()
        if magstimType == 'Magstim':
            self._magstim = virtualMagstim(self._magstimConn)
        elif magstimType == 'BiStim':
            self._magstim = virtualBiStim(self._magstimConn)
        elif magstimType == 'Rapid':
            self._magstim = virtualRapid(self._magstimConn)
        else:
            pass
            # THROW ERROR
        self._magstim.daemon = True
        self.lock = threading.Lock()

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
            if not self.serialWriteQueue.empty():
                try:
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
                                self.serialReadQueue.put_nowait([True,self._portConn.recv()])
                            #Otherwise just get rid of the reply from the pipe
                            else:
                                self._portConn.recv()
                        else:
                            self.serialReadQueue.put_nowait([False,'Timed out while waiting for response.'])
                except Queue.Empty:
                    pass
        #If we get here, it's time to shutdown the serial port controller
        self._portConn.close()
        self._magstimConn.close()
        return