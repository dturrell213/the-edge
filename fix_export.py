code = open('main.py').read()
old = 'action="store_true")    day.add_argument("--tomorrow"'
new = 'action="store_true")\n    day.add_argument("--tomorrow"'
if old in code:
    code = code.replace(old, new)
    open('main.py', 'w').write(code)
    print('Fixed')
else:
    print('Not found')