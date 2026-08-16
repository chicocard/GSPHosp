import os
import sys
from pathlib import Path
import traceback

def check_project():
    sys.path.insert(0, r"d:\papapapa\GSPHosp-Novo")
    import papahr
    
    # Let's import all submodules
    p = Path(r"d:\papapapa\GSPHosp-Novo\papahr")
    errors = []
    for root, dirs, files in os.walk(p):
        for f in files:
            if f.endswith('.py') and f != '__init__.py' and f != '_gen_stubs.py':
                rel = Path(root).relative_to(p)
                mod_parts = ["papahr"]
                if str(rel) != '.':
                    mod_parts.extend(list(rel.parts))
                mod_parts.append(f[:-3])
                mod = ".".join(mod_parts)
                try:
                    __import__(mod)
                except Exception as e:
                    errors.append(f"Error importing {mod}: {e}")
                    # traceback.print_exc()

    with open("d:/papapapa/GSPHosp-Novo/import_test.txt", "w", encoding="utf-8") as f:
        f.write("Import errors:\n")
        f.write("\n".join(errors))

if __name__ == "__main__":
    check_project()
