# MagPy

A Python toolbox for controlling Magstim TMS stimulators via serial communication.

Previously, MagPy only supported Rapid Magstim stimulators with software version 6 or lower. The most recent version (1.2.0b1) is a work-in-progress to update MagPy to support software versions up to 10. There may be bugs!

## Installation

MagPy can be installed easily using the pip package manager:

```
pip install MagPy_TMS
```

Alternatively, you can download the contents of the `magpy` folder and copy them to your PATH or to the same directory as your python script.

## Usage

Check the Wiki (https://github.com/nicolasmcnair/magpy/wiki) for details on how use MagPy.

Example:

```python
from magpy import Magstim
from time import sleep

magstim = Magstim(address='COM1')
magstim.connect()
magstim_info = magstim.getParameters()
magstim.arm()
sleep(2.0) # wait for magstim to arm
magstim.fire()
magstim.disconnect()
```

**Note**: If connecting to a Magstim on a computer running macOS, the address of the serial port you use to create the Magstim object must be the `/dev/cu.*` address for the port and not the `/dev/tty.*` address. Using the `tty` address will create the object successfully, but will result in numerous communication issues with the device.

## Recent Updates
06-02-19: After identifying an error in the official documentation, the rapid.getChargeDelay and rapid.setChargeDelay methods should now be working with version 1.2.0b1

30-01-19: Versions 1.2.0b1 and 1.1.2 should now be fully compatible with Python 3

29-01-19: Fixed an error with attempting to call a serial port property ("TypeError: 'int' object is not callable")
