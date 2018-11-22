# MagPy

A Python toolbox for controlling Magstim TMS stimulators via serial communication.

Currently, MagPy supports Rapid Magstim stimulators with software version 6 or lower. The most recent version (1.2b found in the 'magpy' folder) is a work-in-progress to update MagPy to support software versions up to 10. There may be bugs!

## Installation

MagPy can be installed easily using the pip package manager, provided the host machine also has git installed:

```
pip install git+https://github.com/nicolasmcnair/magpy.git
```

## Usage

Check the Wiki (https://github.com/nicolasmcnair/magpy/wiki) for details on how use MagPy.

Example:

```python
from magpy import BiStim
from time import sleep

magstim = BiStim(address='COM1')
magstim.connect()
magstim_info = magstim.getParameters()
magstim.arm()
sleep(2.0) # wait for magstim to arm
magstim.fire()
magstim.disconnect()
```

**Note**: If connecting to a MagStim on a computer running macOS, the address of the serial port you use to create the MagStim object must be the `/dev/cu.*` address for the port and not the `/dev/tty.*` address. Using the `tty` address will create the object successfully, but will result in numerous communication issues with the device.
