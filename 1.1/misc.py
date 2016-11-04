# -*- coding: utf-8 -*-
"""
Created on Fri Nov 04 14:08:10 2016

Miscellaneous magpy functions

@author: Nicolas McNair
"""
import serial
from multiprocessing import Process
from sys import version_info, platform
#switch timer based on platform
if platform == 'win32':
    # On Windows, use time.clock
    from time import clock
    defaultTimer = clock
else:
    # On other platforms use time.time
    from time import time    
    defaultTimer = time

class serialPortController(Process):
    """
    The class creates a Python process which has direct control of the serial port. Commands for relaying via the serial port are received from separate Python processes via Queues.
    
    N.B. To start the process you must call start() from the parent Python process.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for receiving commands to be written to the Magstim unit via the serial port
    serialReadQueue (multiprocessing.Queue): a Queue for returning automated replies from the Magstim unit when requested
    """    
    def __init__(self,address,serialWriteQueue,serialReadQueue):
        Process.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._serialReadQueue = serialReadQueue
        self._address = address

    def run(self):
        """
        Continuously monitor the serialWriteQueue for commands from other Python processes to be sent to the Magstim.
        
        When requested, will return the automated reply from the Magstim unit to the calling process via the serialReadQueue.
        
        N.B. This should be called via start() from the parent Python process.
        """
        
        #N.B. most of these settings are actually the default in PySerial, but just being careful.
        self._port = serial.Serial(port=self._address,
                                    baudrate=9600,
                                    bytesize=serial.EIGHTBITS,
                                    stopbits=serial.STOPBITS_ONE,
                                    parity=serial.PARITY_NONE,
                                    xonxoff=False)
            
        #Make sure the RTS pin is set to off
        self._port.setRTS(False)
            
        #Set up version compatibility
        if version_info>=(3,0):
            self._port.write_timeout = 0.3
            self._port.portFlush = self._port.reset_input_buffer
            self._port.anyWaiting = self._port.in_waiting
        else:
            self._port.writeTimeout=0.3
            self._port.portFlush = self._port.flushInput
            self._port.anyWaiting = self._port.inWaiting
        
        #This continually monitors the serialWriteQueue for write requests
        while True:
            message,reply,readBytes = self._serialWriteQueue.get()
            #If the first part of the message is None this signals the process to close the port and stop
            if message is None:
                break
            #If the first part of the message is a 1 this signals the process to trigger a quick fire using the RTS pin
            elif message == 1:
                self._port.setRTS(True)
            #If the first part of the message is a -1 this signals the process to reset the RTS pin
            elif message == -1:                
                self._port.setRTS(False)
            #Otherwise, the message is a command string
            else:
                #If there's any rubbish in the input buffer clear it out
                if self._port.anyWaiting():
                    self._port.portFlush()
                #Try writing to the port
                try:
                    self._port.write(message)
                except serial.SerialTimeoutException:
                    self._serialReadQueue.put([False,'Timed out while sending command.'])
                #If we want a reply, read the response from the Magstim and place it in the serialReadQueue
                if reply:
                    try:
                        self._serialReadQueue.put([True,self._port.read(readBytes)])
                    except serial.SerialTimeoutException:
                        self._serialReadQueue.put([False,'Timed out while waiting for response.'])
                #Otherwise just get rid of the reply from the input buffer
                else:
                    self._port.read(readBytes)
        #If we get here, it's time to shutdown the serial port controller
        self._port.close()
        return

class connectionRobot(Process):
    """
    The class creates a Python process which sends an 'enable remote control' command to the Magstim via the serialPortController process every 500ms.
    
    N.B. To start the process you must call start() from the parent Python process.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for sending commands to be written to the Magstim unit via the serialPortController process
    updateTimeQueue (multiprocessing.Queue): a Queue for receiving requests from the parent Python process to delay sending its next command
    """ 
    def __init__(self,serialWriteQueue,_updateRobotQueue):
        Process.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._updateRobotQueue = _updateRobotQueue
        self._stopped = False
        self._paused = True
        self._nextPokeTime = None
        
    def run(self):
        """
        Continuously send commands to the serialPortController process every 500ms, while also monitoring the updateTimeQueue for commands from the parent Python process if this should be delayed.
        
        N.B. This should be called via start() from the parent Python process.
        """
        
        #This sends an "enable remote control" command to the serial port controller every 500ms
        while True:
            #If the robot is currently paused, wait until we get a None (stop) or a 1 (start/resume) in the queue
            while self._paused:
                message = self._updateRobotQueue.get()
                if message is None:
                    self._stopped = True
                elif message == 1:
                    self._paused = False
            #Check if we're stopping the robot
            if self._stopped:
                break
            #Update next poll time to 500 ms
            self._nextPokeTime = defaultTimer() + 0.5
            #While waiting for next poll...
            while defaultTimer() < self._nextPokeTime:
                #...check to see if there has been an update send from the parent magstim object
                if not self._updateRobotQueue.empty():
                    message = self._updateRobotQueue.get()
                    #If the message is None this signals the process to stop
                    if message is None:
                        self._stopped = True
                        break
                    #If the message is -1, this signals the process to pause
                    elif message == -1:
                        self._paused = True
                        break
                    #Any other message is signals a command has been sent to the serial port controller, so bump the next poke time by 500ms
                    else:
                        self._nextPokeTime = defaultTimer() + 0.5
            #If we made it to the the next poll time without breaking, send a poll to the port controller
            else:
                self._serialWriteQueue.put(('Q@n',None,3))
        #If we get here, it's time to shutdown the robot
        return
        
#Calculate checksum for command
def calcCRC(command):
    """Return the CRC checksum for the command string."""
    #Convert command string to sum of ASCII values
    commandSum = sum(bytearray(command))
    #Convert command sum to binary, then invert and return 8-bit character value
    return chr(~commandSum & 0xff) 

def parseMagstimResponse(responseString,responseType):
    """Interprets responses sent from the Magstim unit."""
    #Get ASCII code of first data character
    temp = ord(responseString.pop(0))
    #Interpret bits
    magstimResponse = {'instr':{'standby':        temp & 1,
                                'armed':         (temp >> 1) & 1,
                                'ready':         (temp >> 2) & 1,
                                'coilPresent':   (temp >> 3) & 1,
                                'replaceCoil':   (temp >> 4) & 1,
                                'errorPresent':  (temp >> 5) & 1,
                                'errorType':     (temp >> 6) & 1,
                                'remoteStatus':  (temp >> 7) & 1}}
    
    #If a Rapid system and response includes rTMS status     
    if responseType in {'instrRapid','rapidParam'}:
        #Get ASCII code of second data character        
        temp = ord(responseString.pop(0))
        #Interpret bits; Note: seventh bit is not used
        magstimResponse['rapid'] = {'enhancedPowerMode':        temp & 1,
                                    'train':                   (temp >> 1) & 1,
                                    'wait':                    (temp >> 2) & 1,
                                    'singlePulseMode':         (temp >> 3) & 1,
                                    'hvpsuConnected':          (temp >> 4) & 1,
                                    'coilReady':               (temp >> 5) & 1,
                                    'modifiedCoilAlgorithm':   (temp >> 7) & 1}
    
    #If requesting parameter settings or coil temperature
    if responseType == 'bistimParam':
        magstimResponse['bistimParam'] = {'powerA':   int(''.join(responseString[0:3])),
                                          'powerB':   int(''.join(responseString[3:6])),
                                          'ppOffset': int(''.join(responseString[6:9]))}
    
    elif responseType == 'magstimParam':
        magstimResponse['magstimParam'] = {'power': int(''.join(responseString[0:3]))}
    
    elif responseType == 'rapidParam':
        magstimResponse['rapidParam'] = {'power':     int(''.join(responseString[0:3])),
                                         'frequency': int(''.join(responseString[3:7])) / 10.0,
                                         'nPulses':   int(''.join(responseString[7:11])),
                                         'duration':  int(''.join(responseString[11:14])) / 10.0,
                                         'wait':      int(''.join(responseString[14:17]))}
    
    elif responseType == 'magstimTemp':
        magstimResponse['magstimTemp'] = {'coil1Temp': int(''.join(responseString[0:3])) / 10.0,
                                          'coil2Temp': int(''.join(responseString[3:6])) / 10.0}
    
    return magstimResponse
