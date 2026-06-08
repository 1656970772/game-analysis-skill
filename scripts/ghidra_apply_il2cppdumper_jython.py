# @runtime Jython
# Headless-friendly Il2CppDumper script.json importer for Ghidra.
# Usage: -postScript ghidra_apply_il2cppdumper_jython.py <path-to-script.json>

import json

processFields = [
    "ScriptMethod",
    "ScriptString",
    "ScriptMetadata",
    "ScriptMetadataMethod",
    "Addresses",
]

baseAddress = currentProgram.getImageBase()
USER_DEFINED = ghidra.program.model.symbol.SourceType.USER_DEFINED


def get_addr(addr):
    return baseAddress.add(addr)


def clean_name(name):
    return unicode(name).replace(u" ", u"-")


def set_name(addr, name):
    createLabel(addr, clean_name(name), True, USER_DEFINED)


def make_function(start):
    if getFunctionAt(start) is None:
        createFunction(start, None)


args = getScriptArgs()
if len(args) < 1:
    raise Exception("Expected Il2CppDumper script.json path as first script argument")

data = json.loads(open(args[0], "rb").read().decode("utf-8"))

if "ScriptMethod" in data and "ScriptMethod" in processFields:
    items = data["ScriptMethod"]
    monitor.initialize(len(items))
    monitor.setMessage("IL2CPP methods")
    for item in items:
        set_name(get_addr(item["Address"]), item["Name"])
        monitor.incrementProgress(1)

if "ScriptString" in data and "ScriptString" in processFields:
    items = data["ScriptString"]
    monitor.initialize(len(items))
    monitor.setMessage("IL2CPP strings")
    for index, item in enumerate(items, start=1):
        addr = get_addr(item["Address"])
        createLabel(addr, "StringLiteral_" + str(index), True, USER_DEFINED)
        setEOLComment(addr, unicode(item["Value"]))
        monitor.incrementProgress(1)

if "ScriptMetadata" in data and "ScriptMetadata" in processFields:
    items = data["ScriptMetadata"]
    monitor.initialize(len(items))
    monitor.setMessage("IL2CPP metadata")
    for item in items:
        addr = get_addr(item["Address"])
        set_name(addr, item["Name"])
        setEOLComment(addr, unicode(item["Name"]))
        monitor.incrementProgress(1)

if "ScriptMetadataMethod" in data and "ScriptMetadataMethod" in processFields:
    items = data["ScriptMetadataMethod"]
    monitor.initialize(len(items))
    monitor.setMessage("IL2CPP metadata methods")
    for item in items:
        addr = get_addr(item["Address"])
        set_name(addr, item["Name"])
        setEOLComment(addr, unicode(item["Name"]))
        monitor.incrementProgress(1)

if "Addresses" in data and "Addresses" in processFields:
    items = data["Addresses"]
    monitor.initialize(len(items))
    monitor.setMessage("IL2CPP functions")
    for addrValue in items:
        make_function(get_addr(addrValue))
        monitor.incrementProgress(1)

print "Il2CppDumper script.json imported into Ghidra"
