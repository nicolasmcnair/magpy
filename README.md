# MagPy

A Python toolbox for controlling Magstim TMS stimulators via serial communication.

Previously, MagPy only supported Rapid Magstim stimulators with software version 8 or lower. The most recent version (1.2.0b1) is a work-in-progress to update MagPy to support software versions up to 11. There may be bugs!

**Important Note: In versions prior to 1.2, all parameters must be supplied in their lowest unit of resolution. For BiStim<sup>2</sup> stimulators, this means that the interpulse interval should be supplied in ms if in normal mode, and tenths of a millisecond in high-resolution mode. For Rapid<sup>2</sup> stimulators, this means that frequency should be supplied in tenths of a Hz and duration in tenths of a second. From version 1.2 onwards, all units must be supplied in their base unit: ms for interpulse interval (regardless of mode), Hz for frequency, and seconds for duration. See the help description for each command for details about what unit it is expecting.**

## Installation

MagPy can be installed easily using the pip package manager:

```
pip install MagPy_TMS
```

Alternatively, you can download the contents of the `magpy` folder and copy them to your PATH or to the same directory as your python script.

## Usage

Check the Wiki (https://github.com/nicolasmcnair/magpy/wiki) for details on how use MagPy.

**N.B. For Rapid<sup>2</sup> stimulators, the unlock code should be supplied on its own (i.e., without the command character 'Q' or the CRC). See the commented out command in the example below.**

Example:

```python
from magpy import magstim

if __name__ == "__main__":
    myMagstim = magstim.Magstim(address='COM1')
    #myMagstim = magstim.Rapid(address='COM1', superRapid=1, unlockCode='xxxx-xxxxxxxx-xx')
    myMagstim.connect()
    errorCode,parameterInfo = myMagstim.getParameters()
    myMagstim.arm(delay=True)
    myMagstim.fire()
    myMagstim.disconnect()
```

**Note**: If connecting to a Magstim on a computer running macOS, the address of the serial port you use to create the Magstim object must be the `/dev/cu.*` address for the port and not the `/dev/tty.*` address. Using the `tty` address will create the object successfully, but will result in numerous communication issues with the device.

## Version Summary
| MagPy Version  | Magstim Software Version | Python Version |
|:--------------:|:------------------------:|:--------------:|
| 1.0            |            <=6           |        2       |
| 1.1            |            <=6           |        2       |
| 1.1.1          |            <=8           |        2       |
| 1.1.2          |            <=8           |      2 & 3     |
| 1.2.0b         |            <=11          |      2 & 3     |

**Note**: Magstim Software Version compatibility only concerns Rapid<sup>2</sup> stimulators; all versions should be compatible with 200<sup>2</sup> and BiStim<sup>2</sup> stimulators.

## Recent Updates
04-03-19: Fixed a bug when parsing the returned software version number from a Rapid<sup>2</sup>

04-03-19: Fixed error in poke() command for stimulators with software version 9+

22-02-19: Missing 'else' statement on line 1236 causing import failure

21-02-19: rapid.setChargeDelay() now returns the correct response from the Rapid for software versions 11+

15-02-19: Fixed bug in version 1.2.0b in which the rapid.fire() method accidentally called itself (causing a recursion) rather than calling the parent method

15-02-19: Fixed bug in versions 1.1.2 and 1.2.0b where checking the version of the Magstim would cause an exception

14-02-19: Fixed a potential bug where MagPy might lose remote control over the Magstim if left idle for too long while disarmed

14-02-19: Fixed bug in version 1.2.0b if an unlock code is supplied as a unicode string

11-02-19: Fixed bug with checking pyserial version (shouldn't have affected most people)

06-02-19: After identifying an error in the official documentation, the rapid.getChargeDelay and rapid.setChargeDelay methods should now be working with version 1.2.0b

30-01-19: Versions 1.2.0b and 1.1.2 should now be fully compatible with Python 3

29-01-19: Fixed bug with attempting to call a serial port property ("TypeError: 'int' object is not callable")
