# @runtime Jython
# Decompile selected Ghidra functions listed in a CSV.
# Usage: -postScript ghidra_decompile_targets_csv.py <targets.csv> <output-dir>
# CSV columns: name,rva. RVA may be hex such as 0x73D600 or decimal.

import csv
import os
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor


def parse_int(value):
    text = str(value).strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    return int(text)


def safe_name(name):
    keep = []
    for ch in str(name):
        if ch.isalnum() or ch in ("_", "-", "."):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep)


def row_value(row, *names):
    for name in names:
        value = row.get(name)
        if value:
            return value
    for key, value in row.items():
        clean_key = key
        if clean_key.startswith("\xef\xbb\xbf"):
            clean_key = clean_key[3:]
        if clean_key in names and value:
            return value
    return None


args = getScriptArgs()
if len(args) < 2:
    raise Exception("Expected targets.csv and output directory")

targets_csv = args[0]
out_dir = args[1]
if not os.path.isdir(out_dir):
    os.makedirs(out_dir)

decompiler = DecompInterface()
decompiler.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()
base = currentProgram.getImageBase()
summary = []
seen_stems = {}

with open(targets_csv, "rb") as fp:
    reader = csv.DictReader(fp)
    for row in reader:
        name = row_value(row, "name", "Name")
        rva_text = row_value(row, "rva", "RVA")
        if not name or not rva_text:
            summary.append("%s,,SKIPPED,missing name or rva" % row)
            continue
        rva = parse_int(rva_text)
        addr = base.add(rva)
        func = getFunctionAt(addr)
        if func is None:
            func = createFunction(addr, safe_name(name))
        if func is None:
            summary.append("%s,0x%X,NO_FUNCTION," % (name, rva))
            continue
        result = decompiler.decompileFunction(func, 120, monitor)
        stem = safe_name(name)
        seen_count = seen_stems.get(stem, 0)
        seen_stems[stem] = seen_count + 1
        if seen_count:
            stem = "%s__0x%X" % (stem, rva)
        output_path = os.path.join(out_dir, stem + ".c")
        if result.decompileCompleted():
            code = result.getDecompiledFunction().getC()
            with open(output_path, "wb") as out:
                out.write(code.encode("utf-8"))
            summary.append("%s,0x%X,OK,%s" % (name, rva, output_path))
        else:
            message = result.getErrorMessage()
            with open(output_path + ".error.txt", "wb") as out:
                out.write(str(message).encode("utf-8"))
            summary.append("%s,0x%X,FAILED,%s" % (name, rva, message))

summary_path = os.path.join(out_dir, "decompile_summary.csv")
with open(summary_path, "wb") as out:
    out.write(("name,rva,status,output\n" + "\n".join(summary) + "\n").encode("utf-8"))

print "Selected Ghidra targets decompiled: " + summary_path
