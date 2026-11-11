#!/usr/bin/env python3
"""Add static unwinder/atomic archives to known OpenWrt GCC spec forms."""
import pathlib,subprocess,sys
compiler=sys.argv[1]
path=pathlib.Path(subprocess.check_output([compiler,'-print-file-name=specs'],text=True).strip())
if not path.is_absolute() or not path.is_file():raise SystemExit('Missing SDK specs')
specs=subprocess.check_output([compiler,'-dumpspecs'],text=True)
for old in ['%{static|static-libgcc:-lgcc}','%{static|static-libgcc:-lgcc -lgcc_eh}']:
    if specs.count(old)==1:
        specs=specs.replace(old,'%{static|static-libgcc:-lgcc -lgcc_eh -latomic}',1);break
else:raise SystemExit('Unknown OpenWrt static libgcc specs; inspect rather than guessing')
path.write_text(specs)
