code = open('main.py').read()

# Find the problematic line and fix it
if 'could not convert' in code:
    print("Error is in code")
    
# Replace the entire inner pitcher block with safe version
old_block = 'pitcher_data[team_name] = {\n                        "name": pitcher_name,'

new_block = '''def sf(v, d):
                        try:
                            s = str(v).strip()
                            return float(s) if s not in ["-.--","-.-","---","","None"] else float(d)
                        except:
                            return float(d)
                    pitcher_data[team_name] = {
                        "name": pitcher_name,'''

code = code.replace(old_block, new_block)
open('main.py', 'w').write(code)
print("Done. Lines:", len(code.splitlines()))