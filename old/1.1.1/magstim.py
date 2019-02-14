# -*- coding: utf-8 -*-
"""
Created on Thu Jan 07 2016
Last Modified on Fri Aug 17 2018

Code relating to controlling 200^2, BiStim^2, and Rapid^2 Magstim TMS units

@author: Nicolas McNair
"""
from __future__ import division
from misc import calcCRC, parseMagstimResponse, serialPortController, connectionRobot
from math import floor, ceil
from time import sleep
from multiprocessing import Queue

class MagstimError(Exception):
    pass
  
class Magstim(object):
    """
    The base Magstim class. This is used for controlling 200^2 Magstim units, and acts as a parent class for the BiStim^2 and Rapid^2 sub-classes.
    
    It also creates two additional Python processes; one for the purposes of directly controlling the serial port and another for maintaining constant contact with the Magstim.
    
    N.B. This class can effect limited control over BiStim^2 and Rapid^2 units, however some functionality will not be able to be accessed and return values (including confirmation of commands) may be invalid.
    
         To begin sending commands to the Magstim, and start the additional Python processes, you must first call connect().
    
    Args:
    address (str): The address of the serial port. On Windows this is typically 'COM1' or similar. To create a virtual magstim, set the address to 'virtual'
    """
    def __init__(self, address):
        self._sendQueue = Queue()
        self._receiveQueue = Queue()
        self._setupSerialPort(address)
        self._robotQueue = Queue()
        self._connection.daemon = True
        self._robot = connectionRobot(self._sendQueue, self._robotQueue)
        self._robot.daemon = True
        self._connected = False
            
    def _setupSerialPort(self, address):
        if address.lower() == 'virtual':
            pass
            #from virtual import virtualPortController
            #self._connection = virtualPortController(self.__class__.__name__, self._sendQueue, self._receiveQueue)
        else:
            self._connection = serialPortController(address, self._sendQueue, self._receiveQueue)
    
    def connect(self):
        """ 
        Connect to the Magstim.
        
        This starts the serial port controller, as well as a process that constantly keeps in contact with the Magstim so as not to lose control.
        """
        if not self._connected:
            self._connection.start()
            if not self.remoteControl(enable=True, receipt=True)[0]:
                self._connected = True
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
        #Only process command if toggling remote control, querying parameters, or disarming, or otherwise only if connected to the Magstim
        #N.B. For Rapid stimulators, we first need to have established what version number we are (which sets _parameterReturnBytes) before we can query parameters
        if self._connected or (commandString[0] in {'Q', 'R', 'J', 'F'}) or commandString == 'EA' or (commandString[0] == '\\' and self._parameterReturnBytes is not None):
            #Put command in the send queue to the serial port controller along with what kind of reply is requested and how many bytes to read back from the Magstim
            self._sendQueue.put((commandString + calcCRC(commandString), receiptType, readBytes))
            #If expecting a response, start inspecting the receive queue back from the serial port controller
            if receiptType is not None:
                error, reply = self._receiveQueue.get()
                #If error is true, that means we either couldn't send the command or didn't get anything back from the Magstim
                if error:
                    return (error, reply)
                #If we did get something back from the Magstim, parse the message and the return it
                else:
                    #Check for error messages (error codes 1 and 2 are serial port write/read errors; 8 (below) is for not having established remote control)
                    if reply[0] == '?':
                        return (3,'Invalid command sent.')
                    elif reply[1] == '?':
                        return (4,'Invalid data provided.')
                    elif reply[1] == 'S':
                        return (5,'Command conflicts with current system configuration.')
                    elif reply[0] != commandString[0]:
                        return (6,'Unexpected command confirmation received.')
                    elif calcCRC(reply[0:-1]) != reply[-1]:
                        return (7,'Message contents and CRC value do not match.')
            # If we haven't returned yet, we got a valid message; so update the connection robot if we're connected
            if self._connected:
                if commandString[0] == 'R':
                    self._robotQueue.put(-1)
				elif commandString[:2] == 'EA':
					self._robotQueue.put(1)
                elif commandString[:2] == 'EB':
                    self._robotQueue.put(2)
                else:
                    self._robotQueue.put(0)
            #Then return the parsed response if requested
            return (0, parseMagstimResponse(list(reply[1:-1]), receiptType) if receiptType is not None else None)
        else:
            return (8,'You have not established control of the Magstim unit.')
    
    def remoteControl(self, enable, receipt=False):
        """ 
        Enable/Disable remote control of stimulator. Disabling remote control will first disarm the Magstim unit.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) control
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand('Q@' if enable else 'R@', 'instr' if receipt else None, 3)
    
    def getParameters(self):
        """ 
        Request current parameter settings from the Magstim.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Magstim instrument status ['instr'] and parameter setting ['magstimParam'] dicts, otherwise returns an error string         
        """
        return self._processCommand('J@', 'magstimParam', 12)
    
    def setPower(self, newPower, receipt=False, delay=False, _commandByte='@'):
        """ 
        Set power level for Magstim.
        
        N.B. Allow 100 ms per unit drop in power, or 10 ms per unit increase in power.
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
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
        #If enforcing power change delay, grab current parameters
        if delay:
            error, priorPower = self.getParameters()
            if self.__class__ == 'BiStim':
                priorPower = priorPower['bistimParam']['powerA'] if _commandByte == '@' else priorPower['bistimParam']['powerB']
            else:
                priorPower = priorPower['magstimParam']['power']
        
        error, message = self._processCommand(_commandByte + str(int(newPower)).zfill(3), 'instr' if (receipt or delay) else None, 3)
        
        #If we're meant to delay (and we were able to change the power), then enforce if prior power settings are available
        if delay and not error:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                return (9,'Could not obtain prior power settings.')
            
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
        return self._processCommand('F@', 'magstimTemp', 9)        
    
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
            self._processCommand('Q@', None, 3)
            
    def arm(self, receipt=False, delay=False):
        """ 
        Arm the stimulator.
        
        N.B. You must allow at around 1 s for the stimulator to arm.
        
             If you send an arm() command when the Magstim is already armed, you will receive an non-fatal error reply from the Magstim that the command conflicts with the current settings.
             
             If the unit does not fire for more than 1 min while armed, it will disarm
        
        Args:
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
        delay (bool): enforce delay to allow Magstim time to arm (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string  
        If receipt argument is False:
            None
        """
        error, message = self._processCommand('EB', 'instr' if receipt else None, 3)
        
        #Enforcing arming delay if requested
        if delay:
            sleep(1.2)
        
        return (error, message)
    
    def disarm(self, receipt=False):
        """ 
        Disarm the stimulator.
        
        Args:
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string   
        If receipt argument is False:
            None
        """
        return self._processCommand('EA', 'instr' if receipt else None, 3)
    
    def fire(self, receipt=False):
        """ 
        Fire the stimulator.
        
        N.B. Will only succeed if previously armed.
        
        Args:
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Magstim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand('EH', 'instr' if receipt else None, 3)
    
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
        receipt (bool): whether to return occurence of an error and the automated response from the BiStim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error strin
        If receipt argument is False:
            None
        """
        error,message = self._processCommand('Y@' if enable else 'Z@', 'instr' if receipt else None, 3)
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
        (error,message) = self._processCommand('J@', 'bistimParam', 12)
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
        receipt (bool): whether to return occurence of an error and the automated response from the BiStim unit (defaults to False)
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
        return super(BiStim, self).setPower(newPower, receipt=receipt, delay=delay, _commandByte='@')
    
    def setPowerB(self, newPower, receipt=False, delay=False):
        """ 
        Set power level for BiStim B.
        
        N.B. Allow 100ms per unit drop in power, or 10ms per unit increase in power.
        
             Power output is actually 90% of a 200^2 unit's power output.
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurence of an error and the automated response from the BiStim unit (defaults to False)
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
        return super(BiStim, self).setPower(newPower, receipt=receipt, delay=delay, _commandByte='A')
    
    def setPulseInterval(self, newInterval, receipt=False):
        """ 
        Set interpulse interval.
        
        Args:
        newInterval (int): new interpulse interval in milliseconds (if in low resolution mode) or tenths of a millisecond (if in high resolution mode) (0-999)
        receipt (bool): whether to return occurence of an error and the automated response from the BiStim unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a BiStim instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand('C' + str(int(newInterval)).zfill(3), 'instr' if receipt else None, 3)
    
class Rapid(Magstim):
    """
    This is a sub-class of the parent Magstim class used for controlling Rapid^2 Magstim units. It allows firing in either single-pulse mode or rTMS mode.
    
    In single-pulse mode, the maximum firing frequency is 1 Hz (0.5 Hz if enhanced-power mode is enabled and power is 100 - 110%).
    
    To enable rTMS mode, you must first call rTMSMode(True). To disable rTMS mode, call rTMSMode(False).
    
    N.B. In rTMS mode the maximum frequency allowed is dependent on the power level. Also, there is a dependent relationship between the Duration, NPulses, and Frequency parameter settings.
         Therefore it is recommended either to seek confirmation of any change in settings or to evaluate allowable changes beforehand.
         
         In addition, after each rTMS train there is an enforced delay (minimum 500 ms) before any subsequent train can be initiated or before any rTMS parameter settings can be altered.
    """
    STANDARD = 0
    SUPER = 1
    SUPER_PLUS = 2
    _115V = 0
    _240V = 1
    DEFAULT_VOLTAGE = _240V

    # Maximum allowed rTMS frequency based on voltage and current power setting
    MAX_FREQUENCY = {_240V: {STANDARD:   {x: 50 for x in range(31)}.update({31:46, 32:45, 33:44, 34:42, 35:41, 36:41, 37:39, 38:39, 39:38,  40:37, 
                                                                            41:36, 42:34, 43:33, 44:33, 45:33, 46:33, 47:33, 48:32, 49:31,  50:30,
                                                                            51:30, 52:29, 53:29, 54:28, 55:28, 56:27, 57:27, 58:26, 59:26,  60:25,
                                                                            61:25, 62:24, 63:24, 64:23, 65:23, 66:22, 67:22, 68:21, 69:21,  70:20,
                                                                            71:20, 72:20, 73:20, 74:19, 75:19, 76:19, 77:19, 78:19, 79:18,  80:18,
                                                                            81:18, 82:18, 83:18, 84:18, 85:17, 86:17, 87:17, 88:17, 89:17,  90:17,
                                                                            91:17, 92:16, 93:16, 94:16, 95:16, 96:16, 97:16, 98:15, 99:15, 100:15}),
                             SUPER:      {x:100 for x in range(31)}.update({31:98, 32:95, 33:93, 34:90, 35:88, 36:85, 37:83, 38:80, 39:78,  40:75, 
                                                                            41:73, 42:70, 43:68, 44:65, 45:63, 46:60, 47:58, 48:55, 49:53,  50:50,
                                                                            51:50, 52:49, 53:49, 54:48, 55:48, 56:47, 57:47, 58:46, 59:46,  60:45,
                                                                            61:45, 62:44, 63:44, 64:43, 65:43, 66:42, 67:42, 68:41, 69:41,  70:40,
                                                                            71:40, 72:39, 73:39, 74:38, 75:38, 76:37, 77:37, 78:36, 79:36,  80:35,
                                                                            81:35, 82:34, 83:34, 84:33, 85:33, 86:32, 87:32, 88:31, 89:31,  90:30,
                                                                            91:30, 92:29, 93:29, 94:28, 95:28, 96:27, 97:27, 98:26, 99:26, 100:25}),
                             SUPER_PLUS: {x:100 for x in range(49)}.update({49:98, 50:97, 51:95, 52:93, 53:91, 54:88, 55:88, 56:87, 57:85, 58:84,
                                                                            59:82, 60:80, 61:79, 62:77, 63:74, 64:74, 65:74, 66:74, 67:71, 68:70,
                                                                            69:69, 70:68, 71:68, 72:66, 73:65, 74:62, 75:62, 76:62, 77:60, 78:59,
                                                                            79:58, 80:57, 81:57, 82:56, 83:55, 84:55, 85:53, 86:52, 87:51, 88:50,
                                                                            89:50, 90:49, 91:48, 92:47, 93:46, 94:46, 95:45, 96:44, 97:43, 98:42, 99:42, 100:41})},
                     _115V: {STANDARD:   {x: 36 for x in range(31)}.update({31:35, 32:35, 33:34, 34:33, 35:32, 36:32, 37:31, 38:30, 39:29,  40:28, 
                                                                            41:28, 42:27, 43:26, 44:26, 45:25, 46:25, 47:24, 48:24, 49:23,  50:23,
                                                                            51:22, 52:22, 53:21, 54:21, 55:21, 56:20, 57:20, 58:19, 59:19,  60:19,
                                                                            61:18, 62:18, 63:18, 64:18, 65:17, 66:17, 67:17, 68:16, 69:16,  70:16,
                                                                            71:16, 72:16, 73:15, 74:15, 75:15, 76:15, 77:14, 78:14, 79:14,  80:14,
                                                                            81:14, 82:13, 83:13, 84:13, 85:13, 86:13, 87:12, 88:12, 89:12,  90:12,
                                                                            91:12, 92:12, 93:12, 94:12, 95:11, 96:11, 97:11, 98:11, 99:11, 100:11}),
                             SUPER:      {x: 60 for x in range(38)}.update({38:59, 39:57, 40:55, 41:54, 42:53, 43:52, 44:51, 45:50, 46:49, 47:47,
                                                                            48:46, 49:45, 50:44, 51:43, 52:42, 53:42, 54:41, 55:40, 56:40, 57:39,
                                                                            58:38, 59:38, 60:37, 61:36, 62:36, 63:35, 64:35, 65:34, 66:34, 67:33,
                                                                            68:33, 69:32, 70:32, 71:31, 72:31, 73:30, 74:30, 75:29, 76:29, 77:28,
                                                                            78:28, 79:28, 80:27, 81:27, 82:27, 83:26, 84:26, 85:26, 86:26, 87:25,
                                                                            88:25, 89:25, 90:24, 91:24, 92:24, 93:24, 94:23, 95:23, 96:23, 97:22, 98:22, 99:22, 100:22}),
                             SUPER_PLUS: {x: 60 for x in range(50)}.update({50:58, 51:56, 52:56, 53:54, 54:53, 55:52, 56:51, 57:50, 58:49, 59:48,
                                                                            60:47, 61:46, 62:46, 63:45, 64:44, 65:43, 66:43, 67:42, 68:41, 69:40,
                                                                            70:40, 71:39, 72:39, 73:38, 74:37, 75:37, 76:36, 77:36, 78:35, 79:35,
                                                                            80:34, 81:34, 82:33, 83:33, 84:33, 85:32, 86:32, 87:31, 88:31, 89:30,
                                                                            90:30, 91:30, 92:29, 93:29, 94:28, 95:28, 96:28, 97:27, 98:27, 99:27, 100:25})}}

    def __init__(self, serialConnection, superRapid=STANDARD, voltage=DEFAULT_VOLTAGE):
        super(Rapid, self).__init__(serialConnection)
        self._super = superRapid
        self._voltage = voltage
        self._version = (0, 0, 0)
        self._parameterReturnBytes = None

    def _setupSerialPort(self, address):
        if address.lower() == 'virtual':
            pass
            #from virtual import virtualSerialPortController
            #self._connection = virtualSerialPortController(self.__name__, self._sendQueue, self._receiveQueue, self._super)
        else:
            self._connection = serialPortController(address, self._sendQueue, self._receiveQueue)

    def getVersion(self):
        """ 
        Get Magstim software version number. This is needed when obtaining parameters from the Magstim.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (tuple): if error is 0 (False) returns a tuple containing the version number (in (Major,Minor,Patch) format), otherwise returns an error string
        """
        error, message = self._processCommand('ND', 'version', None)
        #If we didn't receive an error, update the version number and the number of bytes that will be returned by a getParameters() command
        if ~error:
            self._version = message
            if self._version >= (7,):
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
        return self._processCommand('I@', 'error', 6)

    def connect(self, receipt=False):
        """ 
        Connect to the Rapid.
        
        This starts the serial port controller, as well as a process that constantly keeps in contact with the Rapid so as not to lose control.
        It also collects the software version number of the Rapid in order to send the correct command for obtaining parameter settings.

        Args:
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)

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

    def rTMSMode(self, enable, receipt=False):
        """ 
         This is a helper function to enable/disable rTMS mode.
        
        Args:
        enable (bool): whether to enable (True) or disable (False) control
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        # Durations of 1 or 0 are used to toggle repetitive mode on and off
        error,message = self._processCommand('[010' if enable else '[000', 'instrRapid', 4)
        if enable and not error:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                if currentParameters['rapidParam']['frequency'] == 0:
                    updateError,currentParameters = self._processCommand('B0010', 'instrRapid', 4)
                    if updateError:
                        magstimReply = (10,'Could not change frequency from zero.')
            else:
                magstimReply = (9,'Could not get parameters to determine current frequency.')
        
        return (error,message) if receipt else None

    def ignoreCoilSafetySwitch(self, receipt=False):
        """ 
        This allows the stimulator to ignore the state of coil safety interlock switch.
        
        Args:
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand('b@', 'instrRapid' if receipt else None, 4)
    
    def enhancedPowerMode(self, enable, receipt=False):    
        """ 
        Enable/Disable enhanced power mode; allowing intensity to be set to 110%.
        
        N.B. This can only be enabled in single-pulse mode, and lowers the maximum firing frequency to 0.5 Hz.

             Disabling will automatically reduce intensity to 100% if over
        
        Args:
        enable (bool): whether to enable (True) or disable (False) enhanced-power mode
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        return self._processCommand('^@' if enable else '_@', 'instrRapid' if receipt else None, 4)
    
    def setFrequency(self, newFrequency, receipt=False):
        """ 
        Set frequency of rTMS pulse train.
        
        N.B. Changing the Frequency will automatically update the NPulses parameter based on the current Duration parameter setting.
        
             The maximum frequency allowed depends on the current Power level and the regional power settings (i.e., 115V vs. 240V)
        
        Args:
        newFrequency (int/float): new frequency of pulse train in tenths of a Hertz (0-100 for 240V systems, 0-60 for 115V systems)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        error, message = self._processCommand('B' + str(int(newFrequency)).zfill(4), 'instrRapid', 4) 
        #If we didn't get an error, update the other parameters accordingly
        if not error:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                updateError,currentParameters = self._processCommand('D' + str(int(floor(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency']))).zfill(4), 'instrRapid', 4)
                if updateError:
                    magstimReply = (10,'Could not change number of pulses to reflect new frequency.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in number of pulses.')

        return (error, message) if receipt else None
    
    def setNPulses(self, newNPulses, receipt=False):
        """ 
        Set number of pulses in rTMS pulse train.
        
        N.B. Changing the NPulses parameter will automatically update the Duration parameter (this cannot exceed 10 s) based on the current Frequency parameter setting.
        
        Args:
        newNPulses (int): new number of pulses (Version 7+: ?; Version 5+: 1-1000)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        error, message = self._processCommand('D' + str(int(newNPulses)).zfill(4), 'instrRapid', 4)
        #If we didn't get an error, update the other parameters accordingly
        if not error:
            updateError, currentParameters = self.getParameters()
            if not updateError:
                updateError, currentParameters = self._processCommand('[' + str(int(ceil((currentParameters['rapidParam']['nPulses'] / currentParameters['rapidParam']['frequency']) * 10))).zfill(3), 'instrRapid' if receipt else None, 4)
                if updateError:
                    magstimReply = (10,'Could not change duration to reflect new number of pulses.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in duration.')

        return (error, message) if receipt else None
    
    def setDuration(self, newDuration, receipt=False):
        """ 
        Set duration of rTMS pulse train.
        
        N.B. Changing the Duration parameter will automatically update the NPulses parameter based on the current Frequency parameter setting.
        
        Args:
        newDuration (int/float): new duration of pulse train in tenths of a second (Version 7+: ?; Version 5+: 1-10)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        error, message = self._processCommand('[' + str(int(newDuration)).zfill(3), 'instrRapid', 4)
        if not error:
            updateError, currentParameters = self.getParameters()
            if not updateError:
                updateError, currentParameters = self._processCommand('D' + str(int(floor(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency']))).zfill(4), 'instrRapid', 4)
                if updateError:
                    magstimReply = (10,'Could not change number of pulses to reflect new duration.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in number of pulses.')

        return (error, message) if receipt else None
    
    def getParameters(self):
        """ 
        Request current parameter settings from the Rapid.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'], rMTS setting ['rapid'], and parameter setting ['rapidParam'] dicts, otherwise returns an error string
        """
        return self._processCommand('\\@', 'rapidParam', self._parameterReturnBytes)
    
    def setPower(self, newPower, receipt=False, delay=False):
        """ 
        Set power level for the Rapid.
        
        N.B. Allow 100 ms per unit drop in power, or 10 ms per unit increase in power.
        
             Changing the power level can result in automatic updating of the Frequency parameter (if in rTMS mode)
        
        Args:
        newPower (int): new power level (0-100; or 0-110 if enhanced-power mode is enabled)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        delay (bool): enforce delay to allow Rapid time to change Power (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing a Rapid instrument status ['instr'] dict, otherwise returns an error string
        If receipt argument is False:
            None
        """
        error, message = super(Rapid,self).setPower(**locals().update({'_commandByte':'@','receipt':True}))
        
        if not error:
            updateError, currentParameters = self.getParameters()
            if not currentParameters['rapid']['singlePulseMode']:
                if not updateError:
                    maxFrequency = getRapidMaxFrequency(currentParameters['rapidParam']['power'], self._super)
                    if currentParameters['rapidParam']['frequency'] > maxFrequency:
                        if not self.setFreqeuncy(maxFrequency * 10)[0]:
                            magstimReply = (10,'Could not change frequency to reflect new intensity.')
                else:
                    magstimReply = (9,'Could not get parameters to determine current settings.')
        
        return (error,message) if receipt else None