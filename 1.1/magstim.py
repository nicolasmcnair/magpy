# -*- coding: utf-8 -*-
"""
Created on Thu Jan 07 15:26:27 2016

Code relating to controlling 200^2, BiStim^2, and Rapid^2 Magstim TMS units

@author: Nicolas McNair
"""
from __future__ import division
from misc import calcCRC, parseMagstimResponse, connectionRobot
from math import floor, ceil
from time import sleep
from multiprocessing import Queue

class Magstim(object):
    """
    The base Magstim class. This is used for controlling 200^2 Magstim units, and acts as a parent class for the BiStim^2 and Rapid^2 sub-classes.
    
    It also creates two additional Python processes; one for the purposes of directly controlling the serial port and another for maintaining constant contact with the Magstim.
    
    N.B. This class can effect limited control over BiStim^2 and Rapid^2 units, however some functionality will not be able to be accessed and return values (including confirmation of commands) may be invalid.
    
         To begin sending commands to the Magstim, and start the additional Python processes, you must first call connect().
    
    Args:
    address (str): The address of the serial port. On Windows this is typically 'COM1' or similar
    rapidType (bool): This is only used when creating virtual Rapid magstim; determines whether connected to a Super Rapid magstim (connecting to a Super Rapid Plus is not yet available)
    """
    def __init__(self,address):
        self._sendQueue = Queue()
        self._receiveQueue = Queue()
        self._setupSerialPort(address)
        self._robotQueue = Queue()
        self._connection.daemon = True
        self._robot = connectionRobot(self._sendQueue,self._robotQueue)
        self._robot.daemon = True
        self._connected = False
        
    def _setupSerialPort(self,address):
        if address.lower() == 'virtual':
            from virtual import virtualPortController
            self._connection = virtualPortController(self.__class__.__name__,self._sendQueue,self._receiveQueue)
        else:
            from misc import serialPortController
            self._connection = serialPortController(address,self._sendQueue,self._receiveQueue)
    
    def connect(self):
        """ 
        Connect to the Magstim.
        
        This starts the serial port controller, as well as a process that constantly keeps in contact with the Magstim so as not to lose control.
        """
        if not self._connected:
            self._connection.start()
            if not self.remoteControl(enable=True,receipt=True)[0]:
                self._connected = True
                self._robot.start()
            else:
                self._sendQueue.put((None,None,None))
                if self._connection.is_alive():
                    self._connection.join()
                ###raise error
    
    def disconnect(self):
        """ 
        Disconnect from the Magstim.
        
        This stops maintaining contact with the Magstim and turns the serial port controller off.
        """        
        if self._connected:
            self._robotQueue.put(None)
            if self._robot.is_alive():
                self._robot.join()
            self.remoteControl(enable=False)
            self._sendQueue.put((None,None,None))
            if self._connection.is_alive():
                self._connection.join()
            self._connected = False
    
    def _processCommand(self,commandString,receiptType,readBytes):
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
        #Only process command if connected to the Magstim, or if toggling remote control, querying parameters, or disarming
        if self._connected or (commandString[0] in {'Q','R','J','F','\\'}) or commandString == 'EA':
            #Put command in the send queue to the serial port controller along with what kind of reply is requested and how many bytes to read back from the Magstim
            self._sendQueue.put((commandString + calcCRC(commandString), receiptType, readBytes))
                    
            #If expecting a response, start inspecting the receive queue back from the serial port controller
            if receiptType is not None:
                error, reply = self._receiveQueue.get()
                #If error is true, that means we either couldn't send the command or didn't get anything back from the Magstim
                if error:
                    return (error,reply)
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
                if commandString[0] == 'R' or commandString[:2] == 'EA':
                    self._robotQueue.put(-1)
                elif commandString[:2] == 'EB':
                    self._robotQueue.put(1)
                else:
                    self._robotQueue.put(0)
            #Then return the parsed response if requested
            return (0,parseMagstimResponse(list(reply[1:-1]),receiptType)) if receiptType is not None else None
        else:
            return (8,'You have not established control of the Magstim unit.')
    
    def remoteControl(self,enable,receipt=False):
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
        if not enable:
            self.disarm()
        
        return self._processCommand('Q@' if enable else 'R@','instr' if receipt else None,3)
    
    def getParameters(self):
        """ 
        Request current parameter settings from the Magstim.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Magstim instrument status ['instr'] and parameter setting ['magstimParam'] dicts, otherwise returns an error string         
        """
        return self._processCommand('J@','magstimParam',12)
    
    def setPower(self,newPower,receipt=False,delay=False):
        """ 
        Set power level for Magstim.
        
        N.B. Allow 100 ms per unit drop in power, or 10 ms per unit increase in power.
        
        Args:
        newPower (int): new power level (0-100)
        receipt (bool): whether to return occurence of an error and the automated response from the Magstim unit (defaults to False)
        delay (bool): enforce delay to allow Magstim time to change Power (defaults to False)
        
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
            priorPower = priorPower['magstimParam']['power']
        
        magstimReply = self._processCommand('@' + str(int(newPower)).zfill(3),'instr' if (receipt or delay) else None,3)
        
        #If we're meant to delay (and we were able to change the power), then enforce if prior power settings are available
        if delay and not magstimReply[0]:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                return (9,'Could not obtain prior power settings.')
            
        return magstimReply if receipt else None
    
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
        return self._processCommand('F@','magstimTemp',9)        
    
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
            self._processCommand('Q@',None,3)
            
    def arm(self,receipt=False,delay=False):
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
        magstimReply = self._processCommand('EB','instr' if receipt else None,3)
        
        #Enforcing arming delay if requested
        if delay:
            sleep(1.5)
        
        return magstimReply
    
    def disarm(self,receipt=False):
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
        return self._processCommand('EA','instr' if receipt else None,3)
    
    def fire(self,receipt=False):
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
        return self._processCommand('EH','instr' if receipt else None,3)
    
    def resetQuickFire(self):
        """ 
        Reset the RTS pin used for quick firing.
        
        N.B. There must be a few ms between triggering QuickFire and reseting the pin.
        """
        self._sendQueue.put((-1,None,0))
    
    def quickFire(self):
        """ 
        Trigger the stimulator to fire with very low latency using the RTS pin and a custom serial connection.
        """
        self._sendQueue.put((1,None,0))

class BiStim(Magstim):
    """
    This is a sub-class of the parent Magstim class used for controlling BiStim^2 Magstim units. It allows firing in either BiStim mode or Simultaneous Discharge mode.
    
    To enable Simultaneous Discharge mode, you must change the pulseInterval parameter to 0 s (i.e., by calling: setPulseInterval(0)).
    
    N.B. In BiStim mode, the maximum firing frequency is 0.25 Hz. In Simulatenous Discharge mode, the maximum frequency depends on the power level (0.25 - 0.5 Hz)
    """
    
    def highResolutionMode(self,enable,receipt=False):
        """ 
        Enable/Disable high resolution timing of interpulse interval.
        
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
        return self._processCommand('Y@' if enable else 'Z@','instr' if receipt else None,3)
    
    def getParameters(self):
        """ 
        Request current coil temperature from the BiStim.
        
        Returns:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing BiStim instrument status ['instr'] and parameter setting ['bistimParam'] dicts, otherwise returns an error string   
        """
        return self._processCommand('J@','bistimParam',12)
    
    def setPowerA(self,newPower,receipt=False,delay=False):
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
        #If enforcing power change delay, grab current parameters
        if delay:
            error, priorPower = self.getParameters()
            priorPower = priorPower['bistimParam']['powerA']
        
        magstimReply = self._processCommand('@' + str(int(newPower)).zfill(3),'instr' if (receipt or delay) else None,3)
        
        #If we're meant to delay (and we were able to change the power), then enforce if prior power settings are available
        if delay and not magstimReply[0]:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                return (9,'Could not obtain prior power settings in order to enforce delay.')
            
        return magstimReply if receipt else None
    
    def setPowerB(self,newPower,receipt=False,delay=False):
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
        #If enforcing power change delay, grab current parameters
        if delay:
            error, priorPower = self.getParameters()
            priorPower = priorPower['bistimParam']['powerB']
        
        magstimReply = self._processCommand('A' + str(int(newPower)).zfill(3),'instr' if (receipt or delay) else None,3)
        
        #If we're meant to delay (and we were able to change the power), then enforce if prior power settings are available
        if delay and not magstimReply[0]:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                return (9,'Could not obtain prior power settings in order to enforce delay.')
            
        return magstimReply if receipt else None
    
    def setPulseInterval(self,newInterval,receipt=False):
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
        return self._processCommand('C' + str(int(newInterval)).zfill(3),'instr' if receipt else None,3)
    
class Rapid(Magstim):
    """
    This is a sub-class of the parent Magstim class used for controlling Rapid^2 Magstim units. It allows firing in either single-pulse mode or rTMS mode.
    
    In single-pulse mode, the maximum firing frequency is 1 Hz (0.5 Hz if enhanced-power mode is enabled and power is 100 - 110%).
    
    To enable rTMS mode, you must first call rTMSMode(True). To disable rTMS mode, call rTMSMode(False).
    
    N.B. In rTMS mode the maximum frequency allowed is dependent on the power level. Also, there is a dependent relationship between the Duration, NPulses, and Frequency parameter settings.
         Therefore it is recommended either to seek confirmation of any change in settings or to evaluate allowable changes beforehand.
         
         In addition, after each rTMS train there is an enforced 500 ms delay before any subsequent train can be initiated or before any rTMS parameter settings can be altered.
    """
    def __init__(self,serialConnection,superRapid=False):
        self._super = superRapid
        super(Rapid,self).__init__(serialConnection)

    def _setupSerialPort(self,address):
        if address.lower() == 'virtual':
            from virtual import virtualSerialPortController
            self._connection = virtualSerialPortController(self.__name__,self._super,self._sendQueue,self._receiveQueue,self._super)
        else:
            from misc import serialPortController
            self._connection = serialPortController(address,self._sendQueue,self._receiveQueue)

    def rTMSMode(self,enable,receipt=False):
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
        magstimReply = self._processCommand('[010' if enable else '[000','instrRapid',4)
        if enable and not magstimReply[0]:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                if currentParameters['rapidParam']['frequency'] == 0:
                    updateError,currentParameters = self._processCommand('B0010','instrRapid',4)
                    if updateError:
                        magstimReply = (10,'Could not change frequency from zero.')
            else:
                magstimReply = (9,'Could not get parameters to determine current frequency.')
        
        return magstimReply if receipt else None

    def ignoreCoilSafetySwitch(self,receipt=False):
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
        return self._processCommand('b@','instrRapid' if receipt else None,4)
    
    def enhancedPowerMode(self,enable,receipt=False):    
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
        return self._processCommand('^@' if enable else '_@','instrRapid' if receipt else None,4)
    
    def setFrequency(self,newFrequency,receipt=False):
        """ 
        Set frequency of rTMS pulse train.
        
        N.B. Changing the Frequency will automatically update the NPulses parameter based on the current Duration parameter setting.
        
             The maximum frequency allowed depends on the current Power level
        
        Args:
        newFrequency (int): new frequency of pulse train in tenths of a hertz (i.e., per 10 seconds) (1-1000) 
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """        
        magstimReply = self._processCommand('B' + str(int(newFrequency)).zfill(4),'instrRapid',4) 
        if not magstimReply[0]:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                updateError,currentParameters = self._processCommand('D' + str(max(1,int(floor(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency'])))).zfill(4),'instrRapid',4)
                if updateError:
                    magstimReply = (10,'Could not change number of pulses to reflect new frequency.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in number of pulses.')

        return magstimReply if receipt else None
    
    def setNPulses(self,newNPulses,receipt=False):
        """ 
        Set number of pulses in rTMS pulse train.
        
        N.B. Changing the NPulses parameter will automatically update the Duration parameter (this cannot exceed 10 s) based on the current Frequency parameter setting.
        
        Args:
        newNPulses (int): new number of pulses (1-1000)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        magstimReply = self._processCommand('D' + str(max(1,int(newNPulses))).zfill(4),'instrRapid',4)
        if not magstimReply[0]:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                updateError,currentParameters = self._processCommand('[' + str(int(ceil((currentParameters['rapidParam']['nPulses'] / currentParameters['rapidParam']['frequency']) * 10))).zfill(3),'instrRapid' if receipt else None,4)
                if updateError:
                    magstimReply = (10,'Could not change duration to reflect new number of pulses.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in duration.')

        return magstimReply if receipt else None
    
    def setDuration(self,newDuration,receipt=False):
        """ 
        Set duration of rTMS pulse train.
        
        N.B. Changing the Duration parameter will automatically update the NPulses parameter based on the current Frequency parameter setting.
        
        Args:
        newDuration (int): new duration of pulse train in tenths of a second (1-100)
        receipt (bool): whether to return occurence of an error and the automated response from the Rapid unit (defaults to False)
        
        Returns:
        If receipt argument is True:
            :tuple:(error,message):
                error (int): error code (0 = no error; 1+ = error)
                message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'] and rMTS setting ['rapid'] dicts, otherwise returns an error string
        If receipt argument is False:
            None
        """
        magstimReply = self._processCommand('[' + str(int(newDuration)).zfill(3),'instrRapid',4)
        if not magstimReply[0]:
            updateError,currentParameters = self.getParameters()
            if not updateError:
                updateError,currentParameters = self._processCommand('D' + str(max(1,int(floor(currentParameters['rapidParam']['duration'] * currentParameters['rapidParam']['frequency'])))).zfill(4),'instrRapid',4)
                if updateError:
                    magstimReply = (10,'Could not change number of pulses to reflect new duration.')
            else:
                magstimReply = (9,'Could not get parameters to enforce change in number of pulses.')

        return magstimReply if receipt else None
    
    def getParameters(self):
        """ 
        Request current coil temperature from the Rapid.
        
        Returns:
        :tuple:(error,message):
            error (int): error code (0 = no error; 1+ = error)
            message (dict,str): if error is 0 (False) returns a dict containing Rapid instrument status ['instr'], rMTS setting ['rapid'], and parameter setting ['rapidParam'] dicts, otherwise returns an error string
        """
        return self._processCommand('\\@','rapidParam',21)
    
    def setPower(self,newPower,receipt=False,delay=False):
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
        #If enforcing power change delay, grab current parameters
        if delay:
            error, priorPower = self.getParameters()
            priorPower = priorPower['rapidParam']['power']
        
        magstimReply = self._processCommand('@' + str(int(newPower)).zfill(3),'instr',3)
        
        if not magstimReply[0]:
            updateError,currentParameters = self.getParameters()
            if not currentParameters['rapid']['singlePulseMode']:
                if not updateError:
                    if self._super:
                        if 30 < currentParameters['rapidParam']['power'] < 50:
                            maxFrequency = ceil(100 - (2.5 * (currentParameters['rapidParam']['power'] - 30)))
                        elif currentParameters['rapidParam']['power'] <= 100:
                            maxFrequency = ceil(50 - (0.5 * (currentParameters['rapidParam']['power'] - 50)))
                        else:
                            maxFrequency = 25
                    else:
                        if 30 < currentParameters['rapidParam']['power'] < 38:
                            maxFrequency = floor(46 - (1.2 * (currentParameters['rapidParam']['power'] - 31)))
                        elif currentParameters['rapidParam']['power'] < 43:
                            maxFrequency = floor(37 - ((4/3) * (currentParameters['rapidParam']['power'] - 40)))
                        elif currentParameters['rapidParam']['power'] < 47:
                            maxFrequency = 33
                        elif currentParameters['rapidParam']['power'] < 50:
                            maxFrequency = ceil(30 - (currentParameters['rapidParam']['power'] - 50))
                        elif currentParameters['rapidParam']['power'] < 70:
                            maxFrequency = int(30 - (0.5 * (currentParameters['rapidParam']['power'] - 50)))
                        elif currentParameters['rapidParam']['power'] <= 100:
                            maxFrequency = int(20 - ((1/6) * (currentParameters['rapidParam']['power'] - 70)))
                    if currentParameters['rapidParam']['frequency'] > maxFrequency:
                        if not self.setFreqeuncy(maxFrequency * 10)[0]:
                            magstimReply = (10,'Could not change frequency to reflect new intensity.')
                else:
                    magstimReply = (9,'Could not get parameters to determine current settings.')
        
        if delay and not magstimReply[0]:
            if not error:
                if newPower > priorPower:
                    sleep((newPower - priorPower) * 0.01)
                else:
                    sleep((priorPower - newPower) * 0.1)
            else:
                magstimReply = (9,'Could not obtain prior power settings in order to enforce delay.')
        
        return magstimReply if receipt else None
