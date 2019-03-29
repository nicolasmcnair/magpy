#!/usr/bin/env python

from setuptools import setup


install_packages = ['magpy']

setup(
	name='MagPy',
	version='1.2.0b1',
	description='A Python toolbox for controlling Magstim TMS stimulators via serial communication',
	author='Nicolas McNair',
	author_email='nicolas.mcnair@sydney.edu.au',
	url='http://github.com/nicolasmcnair/magpy',
	packages=['magpy'],
	python_requires='>=2.7, !=3.0.*, !=3.1.*, !=3.2.*',
	install_requires=['PySerial']

)