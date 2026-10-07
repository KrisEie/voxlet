"""Prepare COM bindings and report the desktop site-packages directory."""
import comtypes.client
import sysconfig

comtypes.client.GetModule('UIAutomationCore.dll')
print(sysconfig.get_path('purelib'))
