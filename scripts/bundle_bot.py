#!/usr/bin/env python3
import sys
from pathlib import Path

def bundle(bot_dir, output_file):
    bot_path = Path(bot_dir)
    
    # Brute-force order of files to satisfy dependencies without a real AST parser
    files_in_order = [
        "constants.py",
        "features.py",
        "core.py",
        "gunner.py",
        "builder.py",
        "main.py"
    ]
    
    local_modules = [f.replace(".py", "") for f in files_in_order]
    
    bundled_code = []
    bundled_code.append("# --- AUTO-GENERATED BUNDLED BOT ---\n")
    
    for filename in files_in_order:
        filepath = bot_path / filename
        if not filepath.exists():
            continue
            
        bundled_code.append(f"\n# {'='*40}\n")
        bundled_code.append(f"# {filename}\n")
        bundled_code.append(f"# {'='*40}\n")
        
        with open(filepath, "r") as f:
            lines = f.readlines()
            
        i = 0
        while i < len(lines):
            line = lines[i]
            is_local_import = False
            
            # Check if this line is importing one of our local files
            for mod in local_modules:
                if line.startswith(f"from {mod} import"):
                    is_local_import = True
                    # Handle multi-line imports wrapped in parentheses
                    if "(" in line and ")" not in line:
                        while i < len(lines) and ")" not in lines[i]:
                            i += 1
                    break
                elif line.startswith(f"import {mod}"):
                    is_local_import = True
                    break
                    
            if not is_local_import:
                bundled_code.append(line)
            
            i += 1
            
    with open(output_file, "w") as f:
        f.writelines(bundled_code)
    
    print(f"Bundled {bot_dir} into {output_file} successfully!")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python bundle_bot.py <bot_dir> <output_file>")
        sys.exit(1)
    bundle(sys.argv[1], sys.argv[2])
