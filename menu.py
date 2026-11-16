"""
Adds the Why Slow? menu item. Imports nothing from the tool: the command
imports it on click, so a broken install can't stop Nuke launching.
"""

try:
    import nuke

    nuke.menu("Nuke").addCommand("Help/Why Slow?", "__import__('whyslow').show()")
    print("\033[32m[whyslow] Why Slow? menu added (Nuke > Help > Why Slow?).\033[0m")

except ImportError:
    import traceback
    print("\033[31m[whyslow] Why Slow? import failed:")
    traceback.print_exc()
    print("\033[0m")

except Exception:
    import traceback
    print("\033[31m[whyslow] Why Slow? menu setup failed:")
    traceback.print_exc()
    print("\033[0m")
