# -*- coding: utf-8 -*-
"""
Code relating to controlling 200^2, BiStim^2, and Rapid^2 Magstim TMS units

@author: Nicolas McNair
"""
from __future__ import division
import serial
from sys import version_info, platform
from os.path import realpath, join, dirname
from os import getcwd
from math import floor
from time import sleep
from multiprocessing import Queue, Process
from functools import partial
from yaml import load
from ast import literal_eval

# Switch timer based on platform
if platform == 'win32':
    # On Windows, use time.clock
    from time import clock
    defaultTimer = clock
else:
    # On other platforms use time.time
    from time import time    
    defaultTimer = time

# Calculate checksum for command
if version_info >= (3,):
    def calcCRC(command):
        """Return the CRC checksum for the command string."""
        # Convert command string to sum of ASCII/byte values
        commandSum = sum(command)
        # Convert command sum to binary, then invert and return 8-bit character value
        return bytearray(chr(~commandSum & 0xff),encoding='latin_1')
else:
    def calcCRC(command):
        """Return the CRC checksum for the command string."""
        # Convert command string to sum of ASCII/byte values
        commandSum = sum(command)
        # Convert command sum to binary, then invert and return 8-bit character value
        return chr(~commandSum & 0xff)

class MagstimError(Exception):
    pass

class serialPortController(Process):
    """
    The class creates a Python process which has direct control of the serial port. Commands for relaying via the serial port are received from separate Python processes via Queues.
    
    N.B. To start the process you must call start() from the parent Python process.
    
    Args:
    serialWriteQueue (multiprocessing.Queue): a Queue for receiving commands to be written to the Magstim unit via the serial port
    serialReadQueue (multiprocessing.Queue): a Queue for returning automated replies from the Magstim unit when requested
    """

    # Error codes
    SERIAL_WRITE_ERR = (1, 'SERIAL_WRITE_ERR: Could not send the command.')
    SERIAL_READ_ERR  = (2, 'SERIAL_READ_ERR:  Could not read the magstim response.')    
    
    def __init__(self, serialConnection, serialWriteQueue, serialReadQueue):
        Process.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._serialReadQueue = serialReadQueue
        self._address = serialConnection

    def run(self):
        """
        Continuously monitor the serialWriteQueue for commands from other Python processes to be sent to the Magstim.
        
        When requested, will return the automated reply from the Magstim unit to the calling process via the serialReadQueue.
        
        N.B. This should be called via start() from the parent Python process.
        """
        
        # N.B. most of these settings are actually the default in PySerial, but just being careful.
        self._port = serial.Serial(port=self._address,
                                   baudrate=9600,
                                   bytesize=serial.EIGHTBITS,
                                   stopbits=serial.STOPBITS_ONE,
                                   parity=serial.PARITY_NONE,
                                   xonxoff=False)
            
        # Make sure the RTS pin is set to off
        self._port.setRTS(False)
            
        # Set up version compatibility
        if int(serial.VERSION.split('.')[0]) >= 3:
            self._port.write_timeout = 0.3
            self._port.portFlush = self._port.reset_input_buffer
            self._port.anyWaiting = lambda:self._port.in_waiting
        else:
            self._port.writeTimeout = 0.3
            self._port.portFlush = self._port.flushInput
            self._port.anyWaiting = self._port.inWaiting
        # This continually monitors the serialWriteQueue for write requests
        while True:
            message, reply, readBytes = self._serialWriteQueue.get()
            # If the first part of the message is None this signals the process to close the port and stop
            if message is None:
                break
            # If the first part of the message is a 1 this signals the process to trigger a quick fire using the RTS pin
            elif message == 1:
                self._port.setRTS(True)
            # If the first part of the message is a -1 this signals the process to reset the RTS pin
            elif message == -1:                
                self._port.setRTS(False)
            # Otherwise, the message is a command string
            else:
                # There shouldn't be any rubbish in the input buffer, but check and clear it just in case
                if self._port.anyWaiting():
                    self._port.portFlush()
                try:
                    # Try writing to the port
                    self._port.write(message)
                    # Read response (this gets a little confusing, as I don't want to rely on timeout to know if there's an error)
                    try:
                        # Read the first byte
                        message = bytearray(self._port.read(1))
                        # If the first returned byte is a 'N', we need to read the version number in one byte at a time to catch the string terminator.
                        if message == b'N':
                            while message[-1] > 0:
                                message += self._port.read(1)
                            # After the end of the version number, read one more byte to grab the CRC
                            message += self._port.read(1)
                        # If the first byte is not '?', then the message was understood so carry on reading in the response (if it was a '?', then this will be the only returned byte).
                        elif message != b'?':
                            # Read the second byte
                            message += self._port.read(1)
                            # If the second returned byte is a '?' or 'S', then the data value supplied either wasn't acceptable ('?') or the command conflicted with the current settings ('S'),
                            # In these cases, just grab the CRC - otherwise, everything is ok so carry on reading the rest of the message
                            message += self._port.read(readBytes - 2) if message[-1] not in {83, 63} else self._port.read(1)
                        # Return the reply if we want it
                        if reply:
                            self._serialReadQueue.put([0, message])
                    except: #serial.SerialException:
                        self._serialReadQueue.put(serialPortController.SERIAL_READ_ERR)
                except: #serial.SerialException:
                    self._serialReadQueue.put(serialPortController.SERIAL_WRITE_ERR)
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
    def __init__(self, serialWriteQueue, updateRobotQueue):
        Process.__init__(self)
        self._serialWriteQueue = serialWriteQueue
        self._updateRobotQueue = updateRobotQueue
        self._stopped = False
        self._paused = True
        self._nextPokeTime = None
        self._connectionCommand = None

    def _setCommand(self, connectionCommand):
        self._connectionCommand = connectionCommand
        
    def run(self):
        """
        Continuously send commands to the serialPortController process at regular intervals, while also monitoring the updateTimeQueue for commands from the parent Python process if this should be delayed, paused, or stopped.
        
        N.B. This should be called via start() from the parent Python process.
        """
        # This sends an "enable remote control" command to the serial port controller every 500ms (if armed) or 5000 ms (if disarmed); only runs once the stimulator is armed
        pokeLatency = 5
        while True:
            # If the robot is currently paused, wait until we get a None (stop) or a non-negative number (start/resume) in the queue
            while self._paused:
                message = self._updateRobotQueue.get()
                if message is None:
                    self._stopped = True
                    self._paused = False
                elif message >= 0:
                    # If message is a 2, that means we've just armed so speed up the poke latency (not sure that's possible while paused, but just in case)
                    if message == 2:
                        pokeLatency = 0.5
                    # If message is a 1, that means we've just disarmed so slow down the poke latency
                    elif message == 1:
                        pokeLatency = 5
                    self._paused = False
            # Check if we're stopping the robot
            if self._stopped:
                break
            # Update next poll time to the next poke latency
            self._nextPokeTime = defaultTimer() + pokeLatency
            # While waiting for next poll...
            while defaultTimer() < self._nextPokeTime:
                # ...check to see if there has been an update send from the parent magstim object
                if not self._updateRobotQueue.empty():
                    message = self._updateRobotQueue.get()
                    # If the message is None this signals the process to stop
                    if message is None:
                        self._stopped = True
                        break
                    # If the message is -1, we've relinquished remote control so signal the process to pause
                    elif message == -1:
                        pokeLatency = 5
                        self._paused = True
                        break
                    # Any other message signals a command has been sent to the serial port controller
                    else:
                        # If message is a 2, that means we've just armed so speed up the poke latency (not sure that's possible while paused, but just in case)
                        if message == 2:
                            pokeLatency = 0.5
                        # If message is a 1, that means we've just disarmed so slow down the poke latency
                        elif message == 1:
                            pokeLatency = 5
                        self._nextPokeTime = defaultTimer() + pokeLatency
            # If we made it all the way to the next poll time, send a poll to the port controller
            else:
                self._serialWriteQueue.put(self._connectionCommand)
        # If we get here, it's time to shutdown the robot
        return
        
class Magstim(object):
    """
    The base Magstim class. This is used for controlling 200^2 Magstim units, and acts as a parent class for the BiStim^2 and Rapid^2 sub-classes.
    
    It also creates two additional Python processes; one for the purposes of directly controlling the serial port and another for maintaining constant contact with the Magstim.
    
    N.B. This class can effect limited control over BiStim^2 and Rapid^2 units, however some functionality will not be able to be accessed and return values (including confirmation of commands) may be invalid.
    
         To begin sending commands to the Magstim, and start the additional Python processes, you must first call connect().
    
    Args:
    serialConnection (str): The address of the serial port. On Windows this is typically 'COM1' or similar. To create a virtual magstim, set the address to 'virtual'
    """
    
    # Hardware error codes (for all types of stimulators)
    INVALID_COMMAND_ERR       = (3,  'INVALID_COMMAND_ERR: Invalid command sent.')
    INVALID_DATA_ERR          = (4,  'INVALID_DATA_ERR: Invalid data provided.')
    COMMAND_CONFLICT_ERR      = (5,  'COMMAND_CONFLICT_ERR: Command conflicts with current system configuration.')
    INVALID_CONFIRMATION_ERR  = (6,  'INVALID_CONFIRMATION_ERR: Unexpected command confirmation received.')
    CRC_MISMATCH_ERR          = (7,  'CRC_MISMATCH_ERR: Message contents and CRC value do not match.')
    NO_REMOTE_CONTROL_ERR     = (8,  'NO_REMOTE_CONTROL_ERR: You have not established control of the Magstim unit.')
    PARAMETER_ACQUISTION_ERR  = (9,  'PARAMETER_ACQUISTION_ERR: Could not obtain prior parameter settings.')
    PARAMETER_UPDATE_ERR      = (10, 'PARAMETER_UPDATE_ERR: Could not update secondary parameter to accommodate primary parameter change.')
    PARAMETER_FLOAT_ERR       = (11, 'PARAMETER_FLOAT_ERR: A float value is not allowed for this parameter.')
    PARAMETER_PRECISION_ERR   = (12, 'PARAMETER_PRECISION_ERR: Only one decimal placed allowed for this parameter.')
    PARAMETER_RANGE_ERR       = (13, 'PARAMETER_RANGE_ERR: Parameter value is outside the allowed range.')
    GET_SYSTEM_STATUS_ERR     = (14, 'GET_SYSTEM_STATUS_ERR: Cannot call getSystemStatus() until software version has been established.')
    SYSTEM_STATUS_VERSION_ERR = (15, 'SYSTEM_STATUS_VERSION_ERR: Method getSystemStatus() is not compatible with your software version.')
    SEQUENCE_VALIDATION_ERR   = (16, 'SEQUENCE_VALIDATION_ERR: You must call validateSequence() before you can run a rTMS train.')
    MIN_WAIT_TIME_ERR         = (17, 'MIN_WAIT_TIME_ERR: Minimum wait time between trains violated. Call isReadyToFire() to check.')
    MAX_ON_TIME_ERR           = (18, 'MAX_ON_TIME_ERR: Maximum on time exceeded for current train.')
    
    @staticmethod
    def parseMagstimResponse(responseString, responseType):
        """Interprets responses sent from the Magstim unit."""
        if responseType == 'version':
            magstimResponse = tuple(int(x) for x in bytes(responseString[1:-1]).strip().split(b'.'))
        else:
            # Get ASCII code of first data character
            temp = responseString.pop(0)
            # Interpret bits
            magstimResponse = {'instr':{'standby':      temp &   1,
                                        'armed':        (temp >> 1) & 1,
                                        'ready':        (temp >> 2) & 1,
                                        'coilPresent':  (temp >> 3) & 1,
                                        'replaceCoil':  (temp >> 4) & 1,
                                        'errorPresent': (temp >> 5) & 1,
                                        'errorType':    (temp >> 6) & 1,
                                        'remoteStatus': (temp >> 7) & 1}}
    
        # If a Rapid system and response includes rTMS status     
        if responseType in {'instrRapid','rapidParam','systemRapid'}:
            # Get ASCII code of second data character        
            temp = responseString.pop(0)
            # Interpret bits
            magstimResponse['rapid'] = {'enhancedPowerMode':      temp & 1,
                                        'train':                 (temp >> 1) & 1,
                                        'wait':                  (temp >> 2) & 1,
                                        'singlePulseMode':       (temp >> 3) & 1,
                                        'hvpsuConnected':        (temp >> 4) & 1,
                                        'coilReady':             (temp >> 5) & 1,
                                        'thetaPSUDetected':      (temp >> 6) & 1,
                                        'modifiedCoilAlgorithm': (temp >> 7) & 1}
    
        # If requesting parameter settings or coil temperature
        if responseType == 'bistimParam':
            magstimResponse['bistimParam'] = {'powerA':   int(''.join(chr(x) for x in responseString[0:3])),
                                              'powerB':   int(''.join(chr(x) for x in responseString[3:6])),
                                              'ppOffset': int(''.join(chr(x) for x in responseString[6:9]))}
    
        elif responseType == 'magstimParam':
            magstimResponse['magstimParam'] = {'power': int(''.join(chr(x) for x in responseString[:3]))}
    
        elif responseType in 'rapidParam':
            # This is a bit of a hack to determine which software version we're dealing with
            if len(responseString) == 20:
                magstimResponse['rapidParam'] = {'power':     int(''.join(chr(x) for x in responseString[0:3])),
                                                 'frequency': int(''.join(chr(x) for x in responseString[3:7])) / 10.0,
                                                 'nPulses':   int(''.join(chr(x) for x in responseString[7:12])),
                                                 'duration':  int(''.join(chr(x) for x in responseString[12:16])) / 10.0,
                                                 'wait':      int(''.join(chr(x) for x in responseString[16:])) / 10.0}
            else:
                magstimResponse['rapidParam'] = {'power':     int(''.join(chr(x) for x in responseString[0:3])),
                                                 'frequency': int(''.join(chr(x) for x in responseString[3:7])) / 10.0,
                                                 'nPulses':   int(''.join(chr(x) for x in responseString[7:11])),
                                                 'duration':  int(''.join(chr(x) for x in responseString[11:14])) / 10.0,
                                                 'wait':      int(''.join(chr(x) for x in responseString[14:])) / 10.0}
    
        elif responseType == 'magstimTemp':
            magstimResponse['magstimTemp'] = {'coil1Temp': int(''.join(chr(x) for x in responseString[0:3])) / 10.0,
                                              'coil2Temp': int(''.join(chr(x) for x in responseString[3:6])) / 10.0}

        elif responseType == 'systemRapid':
            temp = responseString.pop(0)
            magstimResponse['extInstr'] = {'plus1ModuleDetected':       temp & 1,
                                           'specialTriggerModeActive': (temp >> 1) & 1,
                                           'chargeDelaySet':           (temp >> 2) & 1}

        elif responseType == 'error':
            magstimResponse['currentErrorCode'] = ''.join(chr(x) for x in responseString[:-1])

        elif responseType == 'instrCharge':
             magstimResponse['chargeDelay'] = int(''.join(chr(x) for x in responseString))
             
        return magstimResponse

    def __init__(self, serialConnection):
        self._sendQueue = Queue()
        self._receiveQueue = Queue()
        self._setupSerialPort(serialConnection)
        self._robotQueue = Queue()
        self._connection.daemon = True
        self._robot = connectionRobot(self._sendQueue, self._robotQueue)
        self._robot.daemon = True
        self._connected = False
        self._connectionCommand = (b'Q@n', None, 3)
        self._pokeCommand = b'Q@'
        self._queryCommand = partial(self.remoteControl, enable=True, receipt=True)
        
    def _setupSerialPort(self, serialConnection):
        if serialConnection.lower() == 'virtual':
            from _virtual import virtualPortController
            self._connection = virtualPortController(self.__class__.__name__,self._sendQueue,self._receiveQueue)
        else:
            self._connection = serialPortController(serialConnection, self._sendQueue, self._receiveQueue)
    
    def connect(self):
        """ 
        Connect to the Magstim.
        
        This starts the serial port controller, as well as a process that constantly keeps in contact with the Magstim so as not to lose control.
        """
        if not self._connected:
            self._connection.start()
            if not self.remoteControl(enable=True, receipt=True)[0]:
                self._connected = True
                self._robot._setCommand(self._connectionCommand)
                self._robot.start()
            else:
                self._sendQueue.put((None, None, None))
                if self._connection.is_alive():
                    self._connection.join()
                raise MagstimError('Could not establish remote control over the Magstim.')
    
    def disconnect(self):
        """ 
        Disconnect from the Magstim.
        
        This stops maintaining contact with the Magstim and turns the serial port controller off.
        """        
        if self._connected:
            self.disarm()
            self._robotQueue.put(None)
            if self._robot.is_alive():
                self._robot.join()
            self.remoteControl(enable=False)
            self._sendQueue.put((None, None, None))
            if self._connection.is_alive():
                self._connection.join()
            self._connected = False
    
    def _processCommand(self, commandString, receiptType, readBytes):
        """
        Process Magstim command.
        
        Args:
        commandString (str): command and data characters making up the command string (N.B. do not include CRC character)
        reciptType (bool): whether to return the occurrence of any error when executing the command and the automated response from the Magstim unit
        readBytes (int): number of bytes in the response
        
        Returns:
        If receiptType argument is not None:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing one or more Magstim parameter dicts, otherwise returns an error string
        If receiptType argument is None:
            None
        """
        # Unify Python 2 and 3 strings
        commandString = bytearray(commandString)
        # Only process command if toggling remote control, querying parameters, or disarming, or otherwise only if connected to the Magstim
        # N.B. For Rapid stimulators, we first need to have established what version number we are (which sets _parameterReturnBytes) before we can query parameters
        if self._connected or (commandString[0] in {81, 82, 74, 70}) or commandString == b'EA' or (commandString[0] == 92 and self._parameterReturnBytes is not None):
            # Put command in the send queue to the serial port controller along with what kind of reply is requested and how many bytes to read back from the Magstim
            self._sendQueue.put((bytes(commandString + calcCRC(commandString)), receiptType, readBytes))
            # If expecting a response, start inspecting the receive queue back from the serial port controller
            if receiptType is not None:
                error, reply = self._receiveQueue.get()
                # If error is true, that means we either couldn't send the command or didn't get anything back from the Magstim
                if error:
                    return (error, reply)
                # If we did get something back from the Magstim, parse the message and the return it
                else:
                    # Check for error messages
                    if reply[0] == 63:
                        return Magstim.INVALID_COMMAND_ERR
                    elif reply[1] == 63:
                        return Magstim.INVALID_DATA_ERR
                    elif reply[1] == 83:
                        return Magstim.COMMAND_CONFLICT_ERR
                    elif reply[0] != commandString[0]:
                        return Magstim.INVALID_CONFIRMATION_ERR
                    elif ord(calcCRC(reply[:-1])) != reply[-1]:
                        return Magstim.CRC_MISMATCH_ERR
            # If we haven't returned yet, we got a valid message; so update the connection robot if we're connected
            if self._connected:
                if commandString[0] == 82:
                    self._robotQueue.put(-1)
                elif commandString[:2] == b'EA':
                    self._robotQueue.put(1)
                elif commandString[:2] == b'EB':
                    self._robotQueue.put(2)
                else:
                    self._robotQueue.put(0)
            # Then return the parsed response if requested
            return (0, Magstim.parseMagstimResponse(list(reply[1:-1]), receiptType) if receiptType is not None else None)
        else:
            return Magstim.NO_REMOTE_CONTROL_ERR
    
    def remoteControl(self, enable, receipt=False):
        """ 
        Enable/Disable remote control of stimulator. Disabling remote control will first disarm the Magstim unit.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) control
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand(b'Q@' if enable else b'R@', 'instr' if receipt else None, 3)
    
    def getParameters(self):
        """ 
        Request current parameter settings from the Magstim.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Magstim instrument status ['instr'] and parameter setting ['magstimParam'] dicts, otherwise returns an error string         
        """
        return self._processCommand(b'J@', 'magstimParam', 12)
    
    def setPower(self, newPower, receipt=False, delay=False, _commandByte=b'@'):
        """ 
        Set power level for Magstim.
        
        N.B. Allow 100 ms per unit drop in power, or 10 ms per unit increase in power.
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        delay (bool): enforce delay to allow Magstim time to change Power (defaults to False)
        _commandByte should not be changed by the user
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        # Make sure we have a valid power value
        if newPower % 1:
            return Magstim.PARAMETER_FLOAT_ERR
        elif not 0 <= newPower <= 100:
            return Magstim.PARAMETER_RANGE_ERR

        #If enforcing power change delay, grab current parameters
        if delay:
            error, priorPower = self.getParameters()
            if error:
                return Magstim.PARAMETER_ACQUISTION_ERR
            else:
                # Switch keys depending on whether we're returning for a BiStim
                if self.__class__ == 'BiStim':
                    priorPower = priorPower['bistimParam']['powerA'] if _commandByte == b'@' else priorPower['bistimParam']['powerB']
                else:
                    priorPower = priorPower['magstimParam']['power']
        
        error, message = self._processCommand(_commandByte + bytearray(str(int(newPower)).zfill(3),encoding='ascii'), 'instr' if (receipt or delay) else None, 3)
        
        # If we're meant to delay (and we were able to change the power), then enforce if prior power settings are available
        if delay and not error:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                return Magstim.PARAMETER_UPDATE_ERR
            
        return (error, message) if receipt else None
    
    def getTemperature(self):
        """ 
        Request current coil temperature from the Magstim.
        
        N.B. Coil1 and Coil2 refer to the separate windings in a single figure-8 coil connected to the Magstim.
        
             Magstim units will automatically disarm (and cannot be armed) if the coil temperature exceeds 40 degrees celsius.
        
        Returns:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Magstim instrument status ['instr'] and coil temperature ['magstimTemp'] dicts, otherwise returns an error string
        """
        return self._processCommand(b'F@', 'magstimTemp', 9)        
    
    def poke(self, silent=False):
        """ 
        'Poke' the stimulator with an enable remote control command (only if currently connected).
        This should be used prior to any time-senstive commands, such as triggering the magstim to coincide with stimulus presentation. Conservatively, around 40-50ms should
        be enough time to allow for (~20ms if 'silently' poking). This needs to be done to ensure that the ongoing communication with the magstim to maintain remote control
        does not interfere with the sent command. Note that this simply resets the timer controlling this ongoing communication (i.e., incrementing it a further 500 ms).
        
        Args:
        silent (bool): whether to bump polling robot but without sending enable remote control command (defaults to False)
        """
        if silent and self._connected:
            self._robotQueue.put(0)
        else:
            self._processCommand(self._pokeCommand, None, 3)
            
    def arm(self, receipt=False, delay=False):
        """ 
        Arm the stimulator.
        
        N.B. You must allow at around 1 s for the stimulator to arm.
        
             If you send an arm() command when the Magstim is already armed, you will receive an non-fatal error reply from the Magstim that the command conflicts with the current settings.
             
             If the unit does not fire for more than 1 min while armed, it will disarm
        
        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        delay (bool): enforce delay to allow Magstim time to arm (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string  
        If receipt argument is False:
            None
        """
        error, message = self._processCommand(b'EB', 'instr' if receipt else None, 3)
        
        #Enforcing arming delay if requested
        if delay:
            sleep(1.1)
        
        return (error, message)
    
    def disarm(self, receipt=False):
        """ 
        Disarm the stimulator.
        
        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string   
        If receipt argument is False:
            None
        """
        return self._processCommand(b'EA', 'instr' if receipt else None, 3)

    def isArmed(self):
        """ 
        Helper function that returns True if the Magstim is armed, False if not if it could not be determined.
        """
        error,parameters = self._queryCommand()
        return bool(parameters['instr']['armed']) if not error else False

    def isUnderControl(self):
        """ 
        Helper function that returns True if the Magstim is under remote control, False if not or if it could not be determined.
        """
        error,parameters = self._queryCommand()
        return bool(parameters['instr']['remoteStatus']) if not error else False

    def isReadyToFire(self):
        """ 
        Helper function that returns True if the Magstim is ready to fire, False if not or if it could not be determined.
        """
        error,parameters = self._queryCommand()
        return bool(parameters['instr']['ready']) if not error else False
    
    def fire(self, receipt=False):
        """ 
        Fire the stimulator.
        
        N.B. Will only succeed if previously armed.
        
        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand(b'EH', 'instr' if receipt else None, 3)
    
    def resetQuickFire(self):
        """ 
        Reset the RTS pin used for quick firing.
        
        N.B. There must be a few ms between triggering QuickFire and reseting the pin.
        """
        self._sendQueue.put((-1, None, 0))
    
    def quickFire(self):
        """ 
        Trigger the stimulator to fire with very low latency using the RTS pin and a custom serial connection.
        """
        self._sendQueue.put((1, None, 0))

class BiStim(Magstim):
    """
    This is a sub-class of the parent Magstim class used for controlling BiStim^2 Magstim units. It allows firing in either BiStim mode or Simultaneous Discharge mode.
    
    To enable Simultaneous Discharge mode, you must change the pulseInterval parameter to 0 s (i.e., by calling: setPulseInterval(0)).
    
    N.B. In BiStim mode, the maximum firing frequency is 0.25 Hz. In Simulatenous Discharge mode, the maximum frequency depends on the power level (0.25 - 0.5 Hz)
    """
    def __init__(self, serialConnection):
        super(BiStim, self).__init__(serialConnection)
        self._highResolutionMode = False
    
    def highResolutionMode(self, enable, receipt=False):
        """ 
        Enable/Disable high resolution timing of interpulse interval.
        When enabling high-resolution mode, the system will default to the current interval divided by 10.
        When reverting back to low-resolution Mode, the system will default to a 10ms interval.
        N.B. This cannot be changed while the system is armed.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) high-resolution mode
        receipt (bool): whether to return occurrence of an error and the automated response from the BiStim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error strin
        If receipt argument is False:
            None
        """
        error,message = self._processCommand(b'Y@' if enable else b'Z@', 'instr' if receipt else None, 3)
        if not error:
            self._highResolutionMode = enable
        return (error,message)
    
    def getParameters(self):
        """ 
        Request current coil temperature from the BiStim.
        
        Returns:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing BiStim instrument status ['instr'] and parameter setting ['bistimParam'] dicts, otherwise returns an error string   
        """
        (error,message) = self._processCommand(b'J@', 'bistimParam', 12)
        if not error and self._highResolutionMode:
            message['bistimParam']['ppOffset'] /= 10.0
        return (error,message)
    
    def setPowerA(self, newPower, receipt=False, delay=False):
        """ 
        Set power level for BiStim A.
        
        N.B. Allow 100ms per unit drop in power, or 10ms per unit increase in power.
        
             In BiStim mode, power output is actually 90% of a 200^2 unit's power output. In Simulatenous Discharge mode (pulseInterval = 0), power output is actually 113% of a 200^2 unit's power output
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurrence of an error and the automated response from the BiStim unit (defaults to False)
        delay (bool): enforce delay to allow BiStim time to change Power (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        #This is just an alias for the base magstim class method setPower
        return super(BiStim, self).setPower(newPower, receipt=receipt, delay=delay, _commandByte=b'@')
    
    def setPowerB(self, newPower, receipt=False, delay=False):
        """ 
        Set power level for BiStim B.
        
        N.B. Allow 100ms per unit drop in power, or 10ms per unit increase in power.
        
             Power output is actually 90% of a 200^2 unit's power output.
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurrence of an error and the automated response from the BiStim unit (defaults to False)
        delay (bool): enforce delay to allow BiStim time to change Power (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        #This is just an alias for the base magstim class method setPower
        return super(BiStim, self).setPower(newPower, receipt=receipt, delay=delay, _commandByte=b'A')
    
    def setPulseInterval(self, newInterval, receipt=False):
        """ 
        Set interpulse interval.
        
        Args:
        newInterval (int/float): new interpulse interval in milliseconds (Range low-resolution mode: 0-999; Range high-resolution mode: 0-99.9)
        receipt (bool): whether to return occurrence of an error and the automated response from the BiStim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        # If we're in high resolution mode, then convert to tenths of a millisecond
        if self._highResolutionMode:
            newInterval = newInterval * 10
        # Make sure we have a valid ipi value
        if newInterval % 1:
            return Magstim.PARAMETER_PRECISION_ERR if self._highResolutionMode else Magstim.PARAMETER_FLOAT_ERR
        elif not (0 <= newInterval <= 999):
            return Magstim.PARAMETER_RANGE_ERR

        return self._processCommand(b'C' + bytearray(str(int(newInterval)).zfill(3),encoding='ascii'), 'instr' if receipt else None, 3)
    
class Rapid(Magstim):
    """
    This is a sub-class of the parent Magstim class used for controlling Rapid^2 Magstim units. It allows firing in either single-pulse mode or rTMS mode.
    
    In single-pulse mode, the maximum firing frequency is 1 Hz (0.5 Hz if enhanced-power mode is enabled and power is 100 - 110%).
    
    To enable rTMS mode, you must first call rTMSMode(True). To disable rTMS mode, call rTMSMode(False).
    
    N.B. In rTMS mode the maximum frequency allowed is dependent on the power level. Also, there is a dependent relationship between the Duration, NPulses, and Frequency parameter settings.
         Therefore it is recommended either to seek confirmation of any change in settings or to evaluate allowable changes beforehand.
         
         In addition, after each rTMS train there is an enforced delay (minimum 500 ms) before any subsequent train can be initiated or before any rTMS parameter settings can be altered.
    """
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
    else:
        DEFAULT_RAPID_TYPE = config_data['defaultRapidType']
        DEFAULT_VOLTAGE = config_data['defaultVoltage']
        DEFAULT_UNLOCK_CODE = config_data['unlockCode']
        ENFORCE_ENERGY_SAFETY = config_data['enforceEnergySafety']
        DEFAULT_VIRTUAL_VERSION = literal_eval(config_data['virtualVersionNumber'])

    # Load system info file
    with open(join(__location__, 'rapid_system_info.yaml')) as yaml_file:
        system_info = load(yaml_file)
    # Maximum allowed rTMS frequency based on voltage and current power setting
    MAX_FREQUENCY = system_info['maxFrequency']
    # Minimum wait time (s) required for rTMS train. Power:Joules per pulse
    JOULES = system_info['joules']

    def getRapidMinWaitTime(power, nPulses, frequency):
        """ Calculate minimum wait time between trains for given power, frequency, and number of pulses."""
        return max(0.5, (nPulses * ((frequency * Rapid.JOULES[power]) - 1050.0)) / (1050.0 * frequency))

    def getRapidMaxOnTime(power, frequency):
        """ Calculate maximum train duration for given power and frequency. If greater than 60 seconds, will allow for continuous operation for up to 6000 pulses."""
        return 63000.0 / (frequency * Rapid.JOULES[power])

    def getRapidMaxContinuousOperationFrequency(power):
        """ Calculate maximum frequency that will allow for continuous operation (up to 6000 pulses)."""
        return 1050.0 / Rapid.JOULES[power]

    def __init__(self, serialConnection, superRapid=DEFAULT_RAPID_TYPE, unlockCode=DEFAULT_UNLOCK_CODE, voltage=DEFAULT_VOLTAGE, version=DEFAULT_VIRTUAL_VERSION):
        self._super = superRapid
        self._unlockCode = unlockCode
        self._voltage = voltage
        self._version = version if serialConnection.lower() == 'virtual' else (0,0,0)
        super(Rapid, self).__init__(serialConnection)
        # If an unlock code has been supplied, then the Rapid requires a different command to stay in contact with it.
        if self._unlockCode:
            self._connectionCommand = (b'x@G', None, 6)
            self._pokeCommand = b'x@'
            self._queryCommand = self.getSystemStatus
        self._parameterReturnBytes = None
        self._sequenceValidated = False
        self._repetitiveMode = False

    def _setupSerialPort(self, serialConnection):
        if serialConnection.lower() == 'virtual':
            from _virtual import virtualPortController
            self._connection = virtualPortController(self.__class__.__name__,self._sendQueue,self._receiveQueue,superRapid=self._super,unlockCode=self._unlockCode,voltage=self._voltage,version=self._version)
        else:
            self._connection = serialPortController(serialConnection, self._sendQueue, self._receiveQueue)

    def getVersion(self):
        """ 
        Get Magstim software version number. This is needed when obtaining parameters from the Magstim.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (tuple): if error is 0 (False) returns a tuple containing the version number (in (Major,Minor,Patch) format), otherwise returns an error string
        """
        error, message = self._processCommand(b'ND', 'version', None)
        #If we didn't receive an error, update the version number and the number of bytes that will be returned by a getParameters() command
        if not error:
            self._version = message
            if self._version >= (9,):
                self._parameterReturnBytes = 24
            elif self._version >= (7,):
                self._parameterReturnBytes = 22
            else:
                self._parameterReturnBytes = 21
        return (error,message)

    def getErrorCode(self):
        """ 
        Get current error code from Rapid.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and current error code ['errorCode'] dicts, otherwise returns an error string
        """
        return self._processCommand(b'I@', 'error', 6)

    def connect(self, receipt=False):
        """ 
        Connect to the Rapid.
        
        This starts the serial port controller, as well as a process that constantly keeps in contact with the Rapid so as not to lose control.
        It also collects the software version number of the Rapid in order to send the correct command for obtaining parameter settings.

        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)

        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (str): if error is 0 (False) returns a string containing the version number (in (X,X,X) format), otherwise returns an error string
        """
        super(Rapid,self).connect()
        # We have to be able to determine the software version of the Rapid, otherwise we won't be able to communicate properly
        error, message = self.getVersion()
        if error:
            self.disconnect()
            raise MagstimError('Could not determine software version of Rapid. Disconnecting.')

    def disconnect(self):
        """ 
        Disconnect from the Magstim.
        
        This stops maintaining contact with the Magstim and turns the serial port controller off.
        """ 
        #Just some housekeeping before we call the base magstim class method disconnect
        self._sequenceValidated = False
        self._repetitiveMode = False
        return super(Rapid, self).disconnect()

    def rTMSMode(self, enable, receipt=False):
        """ 
         This is a helper function to enable/disable rTMS mode.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) control
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated =  False
        # Durations of 1 or 0 are used to toggle repetitive mode on and off
        if self._version >= (9, 0, 0):
            commandString = b'[0010' if enable else b'[0000'
        else:
            commandString = b'[010' if enable else b'[000'
        error,message = self._processCommand(commandString, 'instrRapid', 4)
        if not error:
            if enable:
                self._repetitiveMode = True
                updateError,currentParameters = self.getParameters()
                if not updateError:
                    if currentParameters['rapidParam']['frequency'] == 0:
                        updateError,currentParameters = self._processCommand(b'B0010', 'instrRapid', 4)
                        if updateError:
                            return Magstim.PARAMETER_UPDATE_ERR
                else:
                    return Magstim.PARAMETER_ACQUISTION_ERR
            else:
                self._repetitiveMode = False
        
        return (error,message) if receipt else None

    def ignoreCoilSafetySwitch(self, receipt=False):
        """ 
        This allows the stimulator to ignore the state of coil safety interlock switch.
        
        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand(b'b@', 'instr' if receipt else None, 3)

    def remoteControl(self, enable, receipt=False):
        """ 
        Enable/Disable remote control of stimulator. Disabling remote control will first disarm the Magstim unit.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) control
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated = False
        if self._unlockCode:
            return self._processCommand(b'Q' + bytearray(self._unlockCode,encoding='latin_1') if enable else b'R@', 'instr' if receipt else None, 3)
        else:
            return self._processCommand(b'Q@' if enable else b'R@', 'instr' if receipt else None, 3)
    
    def enhancedPowerMode(self, enable, receipt=False):    
        """ 
        Enable/Disable enhanced power mode; allowing intensity to be set to 110%.
        
        N.B. This can only be enabled in single-pulse mode, and lowers the maximum firing frequency to 0.5 Hz.

             Disabling will automatically reduce intensity to 100% if over
        
        Args:
        enable (bool): whether to enable (True) or disable (False) enhanced-power mode
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand(b'^@' if enable else b'_@', 'instrRapid' if receipt else None, 4)

    def isEnhanced(self):
        """ 
        Helper function that returns True if the Rapid is in enhanced power mode, False if not if it could not be determined.
        """
        error,parameters = self._queryCommand()
        return bool(parameters['rapid']['enhancedPowerMode']) if not error else False
    
    def setFrequency(self, newFrequency, receipt=False):
        """ 
        Set frequency of rTMS pulse train.
        
        N.B. Changing the Frequency will automatically update the NPulses parameter based on the current Duration parameter setting.
        
             The maximum frequency allowed depends on the current Power level and the regional power settings (i.e., 115V vs. 240V)
        
        Args:
        newFrequency (int/float): new frequency of pulse train in Hertz (0-100 for 240V systems, 0-60 for 115V systems); decimal values are allowed for frequencies up to 30Hz
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated =  False

        # Convert to tenths of a Hz
        newFrequency = newFrequency * 10
        # Make sure we have a valid frequency value
        if newFrequency % 1:
            return Magstim.PARAMETER_PRECISION_ERR
        updateError,currentParameters = self.getParameters()
        if updateError:
            return Magstim.PARAMETER_ACQUISTION_ERR
        else:
            maxFrequency = Rapid.MAX_FREQUENCY[currentParameters['rapidParam']['power']]
            if not (0 <= newFrequency <= maxFrequency):
                return Magstim.PARAMETER_RANGE_ERR

        #Send command
        error, message = self._processCommand(b'B' + bytearray(str(int(newFrequency)).zfill(4),encoding='ascii'), 'instrRapid', 4) 
        #If we didn't get an error, update the other parameters accordingly
        if not error:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                updateError,currentParameters = self._processCommand(b'D' + bytearray(str(int(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency'])).zfill(5 if self._version >= (9, 0, 0) else 4),encoding='ascii'), 'instrRapid', 4)
                if updateError:
                    return Magstim.PARAMETER_UPDATE_ERR
            else:
                return Magstim.PARAMETER_ACQUISTION_ERR

        return (error, message) if receipt else None
    
    def setNPulses(self, newNPulses, receipt=False):
        """ 
        Set number of pulses in rTMS pulse train.
        
        N.B. Changing the NPulses parameter will automatically update the Duration parameter (this cannot exceed 10 s) based on the current Frequency parameter setting.
        
        Args:
        newNPulses (int): new number of pulses (Version 9+: 1-6000; Version 7+: ?; Version 5+: 1-1000?)
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated =  False

        # Make sure we have a valid number of pulses value
        if newNPulses % 1:
            return Magstim.PARAMETER_FLOAT_ERR
        if not (0 <= newNPulses <= 6000):
            return Magstim.PARAMETER_RANGE_ERR

        #Send command
        error, message = self._processCommand(b'D' + bytearray(str(int(newNPulses)).zfill(5 if self._version >= (9, 0, 0) else 4),encoding='ascii'), 'instrRapid', 4)
        #If we didn't get an error, update the other parameters accordingly
        if not error:
            updateError, currentParameters = self.getParameters()
            if not updateError:
                updateError, currentParameters = self._processCommand(b'[' + bytearray(str(int(currentParameters['rapidParam']['nPulses'] / currentParameters['rapidParam']['frequency'])).zfill(4 if self._version >= (9, 0, 0) else 3),encoding='ascii'), 'instrRapid' if receipt else None, 4)
                if updateError:
                    return Magstim.PARAMETER_UPDATE_ERR
            else:
                return Magstim.PARAMETER_ACQUISTION_ERR

        return (error, message) if receipt else None
    
    def setDuration(self, newDuration, receipt=False):
        """ 
        Set duration of rTMS pulse train.
        
        N.B. Changing the Duration parameter will automatically update the NPulses parameter based on the current Frequency parameter setting.
        
        Args:
        newDuration (int/float): new duration of pulse train in seconds (Version 9+: 1-600; Version 7+: ?; Version 5+: 1-10?); decimal values are allowed for durations up to 30s
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated =  False

        # Convert to tenths of a second
        newDuration = newDuration * 10
        # Make sure we have a valid duration value
        if newDuration % 1:
            return Magstim.PARAMETER_PRECISION_ERR
        elif not (0 <= newDuration <= (999 if self._version < (9,0,0) else 9999)):
            return Magstim.PARAMETER_RANGE_ERR

        error, message = self._processCommand(b'[' + bytearray(str(int(newDuration)).zfill(4 if self._version >= (9, 0, 0) else 3),encoding='ascii'), 'instrRapid', 4)
        if not error:
            updateError, currentParameters = self.getParameters()
            if not updateError:
                updateError, currentParameters = self._processCommand(b'D' + bytearray(str(int(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency'])).zfill(5 if self._version >= (9, 0, 0) else 4),encoding='ascii'), 'instrRapid', 4)
                if updateError:
                    return Magstim.PARAMETER_UPDATE_ERR
            else:
                return Magstim.PARAMETER_ACQUISTION_ERR

        return (error, message) if receipt else None
    
    def getParameters(self):
        """ 
        Request current parameter settings from the Rapid.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'], rMTS setting ['rapid'], and parameter setting ['rapidParam'] dicts, otherwise returns an error string
        """
        return self._processCommand(b'\\@', 'rapidParam', self._parameterReturnBytes)
    
    def setPower(self, newPower, receipt=False, delay=False):
        """ 
        Set power level for the Rapid.
        
        N.B. Allow 100 ms per unit drop in power, or 10 ms per unit increase in power.
        
             Changing the power level can result in automatic updating of the Frequency parameter (if in rTMS mode)
        
        Args:
        newPower (int): new power level (0-100; or 0-110 if enhanced-power mode is enabled)
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        delay (bool): enforce delay to allow Rapid time to change Power (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Rapid instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        self._sequenceValidated =  False

        # Make sure we have a valid power value
        if newPower % 1:
            return Magstim.PARAMETER_FLOAT_ERR
        elif not 0 <= newPower <= (110 if self.isEnhanced else 100):
            return Magstim.PARAMETER_RANGE_ERR
        
        error, message = super(Rapid,self).setPower(newPower,True,delay,b'@')
        if not error:
            updateError, currentParameters = self.getParameters()
            if not updateError:
                if not currentParameters['rapid']['singlePulseMode']:
                    maxFrequency = Rapid.MAX_FREQUENCY[self._voltage][self._super][currentParameters['rapidParam']['power']]
                    if currentParameters['rapidParam']['frequency'] > maxFrequency:
                        if not self.setFreqeuncy(maxFrequency)[0]:
                            return Magstim.PARAMETER_UPDATE_ERR
            else:
                return Magstim.PARAMETER_ACQUISTION_ERR
        
        return (error,message) if receipt else None

    def setChargeDelay(self, newDelay, receipt=False):
        """ 
        Set charge delay duration for the Rapid.
        
        Args:
        newDelay (int): new delay duration in seconds (Version 10+: 1-10000; Version 9: 1-2000)
        receipt (bool): whether to return occurrence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Rapid instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        if self._version is None:
            return Magstim.GET_SYSTEM_STATUS_ERR
        elif self._version < (9, 0, 0):
            return Magstim.SYSTEM_STATUS_VERSION_ERR

        self._sequenceValidated =  False
            
        #Make sure we have a valid delay duration value
        if newDelay % 1:
            return Magstim.PARAMETER_FLOAT_ERR

        error, message = self._processCommand(b'n' + bytearray(str(int(newDelay)).zfill(5 if self._version >= (10, 0, 0) else 4),encoding='ascii'), 'systemRapid' if self._version >= (10, 0, 0) 'instrRapid', 6 if self._version >= (10, 0, 0) else 4)
        
        return (error,message) if receipt else None

    def getChargeDelay(self):
        """ 
        Get current charge delay duration for the Rapid.
        
        Returns:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Rapid instrument status ['instr'] dict and charge delay duration ['chargeDelay'] value, otherwise returns an error string
        """
        if self._version is None:
            return Magstim.GET_SYSTEM_STATUS_ERR
        elif self._version < (9, 0, 0):
            return Magstim.SYSTEM_STATUS_VERSION_ERR

        return self._processCommand(b'o@', 'instrCharge', 8 if self._version > (9, 0, 0) else 7)

    def fire(self, receipt=False):
        """ 
        Fire the stimulator. This overrides the base Magstim method in order to check whether rTMS mode is active, and if so whether the sequence has been validated and the min wait time between trains has elapsed
        
        N.B. Will only succeed if previously armed.
        
        Args:
        receipt (bool): whether to return occurrence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        if self._repetitiveMode and Rapid.ENFORCE_ENERGY_SAFETY and not self._sequenceValidated:
            return Magstim.SEQUENCE_VALIDATION_ERR
        else:
            return super(Rapid,self).fire(receipt)

    def quickFire(self):
        """ 
        Trigger the stimulator to fire with very low latency using the RTS pin and a custom serial connection.
        """
        if self._repetitiveMode and Rapid.ENFORCE_ENERGY_SAFETY and not self._sequenceValidated:
            return Magstim.SEQUENCE_VALIDATION_ERR
        else:
            super(Rapid,self).quickFire()

    def validateSequence(self):
        """ 
        Validate the energy consumption for the current rTMS parameters for the Rapid.
        This must be performed before running any new sequence, otherwise calling fire() will return an error.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns 'OK', otherwise returns an error string
        """
        error,parameters = self.getParameters()
        if error:
            return Magstim.PARAMETER_ACQUISTION_ERR
        elif min(parameters['duration'], 60) > Rapid.getRapidMaxOnTime(parameters['rapidParam']['power'], parameters['rapidParam']['frequency']):
            return Magstim.MAX_ON_TIME_ERR
        else:
            self._sequenceValidated = True
            return (0, 'Seqeunce valid.')

    def getSystemStatus(self):
        """ 
        Get system status from the Rapid. Available only on software version of 9 or later.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'], rMTS setting ['rapid'], and extended instrument status ['extInstr'] dicts, otherwise returns an error string
        """
        if self._version is None:
            return Magstim.GET_SYSTEM_STATUS_ERR
        elif self._version >= (9, 0, 0):
            return self._processCommand(b'x@', 'systemRapid', 6)
        else:
            return Magstim.SYSTEM_STATUS_VERSION_ERR