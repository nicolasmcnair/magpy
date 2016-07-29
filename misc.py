# -*- coding: utf-8 -*-
"""
Created on Thu Jan 07 14:08:10 2016

Miscellaneous magpy functions

@author: Nicolas McNair
"""
import multiprocessing
import serial
from sys import version_info, platform
#switch timer based on platform
if platform == 'win32':
    # On Windows, use time.clock
    from time import clock
    default_timer = clock
else:
    # On other platforms use time.time
    from time import time    
    default_timer = time

class serialPortController(multiprocessing.Process):
    """
    The class creates a Python process which has direct control of the serial port. Commands for relaying via the serial port are received from separate Python processes via Queues.
    
    N.B. To start the process you must call start() from the parent Python process.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for receiving commands to be written to the Magstim unit via the serial port
    serialReadQueue (multiprocessing.Queue): a Queue for returning automated replies from the Magstim unit when requested
    """    
    def __init__(self,address,serialWriteQueue,serialReadQueue):      
        multiprocessing.Process.__init__(self)
        self.serialWriteQueue = serialWriteQueue
        self.serialReadQueue = serialReadQueue
        self.address = address

    def run(self):
        """
        Continuously monitor the serialWriteQueue for commands from other Python processes to be sent to the Magstim.
        
        When requested, will return the automated reply from the Magstim unit to the calling process via the serialReadQueue.
        
        N.B. This should be called via start() from the parent Python process.
        """
        #N.B. most of these settings are actually the default in PySerial, but just being careful.
        self.port = serial.Serial(port=self.address,
                                  baudrate=9600,
                                  bytesize=serial.EIGHTBITS,
                                  stopbits=serial.STOPBITS_ONE,
                                  parity=serial.PARITY_NONE,
                                  xonxoff=False)
        
        #Make sure the RTS pin is set to off                          
        self.port.setRTS(False)
        
        #Set up version compatibility                       
        if version_info>=(3,0):
            self.port.write_timeout = 0.3
            self.port.portFlush = self.port.reset_input_buffer
            self.port.anyWaiting = self.port.in_waiting
        else:
            self.port.writeTimeout=0.3            
            self.port.portFlush = self.port.flushInput
            self.port.anyWaiting = self.port.inWaiting
                          
        #This continually monitors the serialWriteQueue for write requests
        while True:
            message,reply,readBytes = self.serialWriteQueue.get()
            #If the first part of the message is None this signals the process to close the port and stop
            if message is None:
                break
            #If the first part of the message is a 1 this signals the process to trigger a quick fire using the RTS pin
            elif message == 1:
                self.port.setRTS(True)
            #If the first part of the message is a -1 this signals the process to reset the RTS pin
            elif message == -1:                
                self.port.setRTS(False)
            #Otherwise, the message is a command string
            else:
                #If there's any rubbish in the input buffer clear it out
                if self.port.anyWaiting():
                    self.port.portFlush()
                #Try writing to the port
                try:
                    self.port.write(message)
                except serial.SerialTimeoutException:
                    readBytes = 0;
                    self.serialReadQueue.put([False,'Timed out while sending command.'])
                #If we want a reply, read the response from the Magstim and place it in the serialReadQueue
                if reply:
                    try:
                        self.serialReadQueue.put([True,self.port.read(readBytes)])
                    except serial.SerialTimeoutException:
                        self.serialReadQueue.put([False,'Timed out while waiting for response.'])
                #Otherwise just get rid of the reply from the input buffer
                else:
                    self.port.read(readBytes)
        #If we get here, it's time to shutdown the serial port controller
        self.port.close()
        return

class connectionRobot(multiprocessing.Process):
    """
    The class creates a Python process which sends an 'enable remote control' command to the Magstim via the serialPortController process every 500ms.
    
    N.B. To start the process you must call start() from the parent Python process.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for sending commands to be written to the Magstim unit via the serialPortController process
    updateTimeQueue (multiprocessing.Queue): a Queue for receiving requests from the parent Python process to delay sending its next command
    """ 
    def __init__(self,serialWriteQueue,updateTimeQueue):
        multiprocessing.Process.__init__(self)
        self.serialWriteQueue = serialWriteQueue
        self.updateTimeQueue = updateTimeQueue
        self._stopped = False
        self.nextPokeTime = None
        
    def run(self):
        """
        Continuously send commands to the serialPortController process every 500ms, while also monitoring the updateTimeQueue for commands from the parent Python process if this should be delayed.
        
        N.B. This should be called via start() from the parent Python process.
        """
        #This sends an "enable remote control" command to the serial port controller every 500ms
        while not self._stopped:
            self.serialWriteQueue.put(('Q@n',None,3))
            self.nextPokeTime = default_timer() + 0.5
            while default_timer() < self.nextPokeTime:
                #Checks to see if there has been an update send from the parent magstim
                if not self.updateTimeQueue.empty():
                    #If the message is None this signals the process to stop
                    if self.updateTimeQueue.get() is None:
                        self._stopped = True
                        break
                    #Any other message is signals a command has been sent to the serial port controller, so bump the next poke time by 500ms
                    else:
                        self.nextPokeTime = default_timer() + 0.5
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
                                'coil_present':  (temp >> 3) & 1,
                                'replace_coil':  (temp >> 4) & 1,
                                'error_present': (temp >> 5) & 1,
                                'error_type':    (temp >> 6) & 1,
                                'remote_status': (temp >> 7) & 1}}
    
    #If a Rapid system and response includes rTMS status     
    if responseType in {'instr_rapid','rapid_param'}:
        #Get ASCII code of second data character        
        temp = ord(responseString.pop(0))
        #Interpret bits; Note: seventh bit is not used
        magstimResponse['rapid'] = {'enhanced_power_mode':      temp & 1,
                                    'train':                   (temp >> 1) & 1,
                                    'wait':                    (temp >> 2) & 1,
                                    'single_pulse_mode':       (temp >> 3) & 1,
                                    'hvpsu_connected':         (temp >> 4) & 1,
                                    'coil_ready':              (temp >> 5) & 1,
                                    'modified_coil_algorithm': (temp >> 7) & 1}
    
    #If requesting parameter settings or coil temperature
    if responseType == 'bistim_param':
        magstimResponse['bistim_param'] = {'power_a':   int(''.join(responseString[0:3])),
                                           'power_b':   int(''.join(responseString[3:6])),
                                           'pp_offset': int(''.join(responseString[6:9]))}
    
    elif responseType == 'magstim_param':
        magstimResponse['magstim_param'] = {'power': int(''.join(responseString[0:3]))}
    
    elif responseType == 'rapid_param':
        magstimResponse['rapid_param'] = {'power':     int(''.join(responseString[0:3])),
                                          'frequency': int(''.join(responseString[3:7])) / 10.0,
                                          'n_pulses':  int(''.join(responseString[7:11])),
                                          'duration':  int(''.join(responseString[11:14])) / 10.0,
                                          'wait':      int(''.join(responseString[14:17]))}
    
    elif responseType == 'magstim_temp':
        magstimResponse['magstim_temp'] = {'coil1_temp': int(''.join(responseString[0:3])) / 10.0,
                                           'coil2_temp': int(''.join(responseString[3:6])) / 10.0}
    
    return magstimResponse