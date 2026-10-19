# PyInstaller build for the Windows release:  pyinstaller combat_sim.spec
# Output: dist/40kCombatSimulator/ (one folder; zip it for a release). See docs/releasing.md.

a = Analysis(
    ['run.py'],
    pathex=['src'],
    datas=[
        ('src/combat_sim/templates', 'combat_sim/templates'),
        ('src/combat_sim/static', 'combat_sim/static'),
        ('data/40k_data.json', 'data'),  # copied to the user's data folder on first run
    ],
    excludes=['openpyxl', 'tkinter', 'pytest', 'IPython', 'matplotlib'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='40kCombatSimulator',
    console=True,  # the console window shows the address and closing it stops the app
)
coll = COLLECT(exe, a.binaries, a.datas, name='40kCombatSimulator')
