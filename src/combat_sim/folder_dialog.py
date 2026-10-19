"""
The native "choose a folder" dialog behind the Setup page's Browse button.

The pages run in a browser, which can't tell the server a folder path, so the server (on the
same computer) opens the dialog itself. Returns the chosen folder, or None if the user
cancelled or no dialog can be shown (then the page's path box still works).
"""


def choose_folder(initial=None, title='Choose the BSData folder'):
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
    except tkinter.TclError:  # No display
        return None
    try:
        root.withdraw()
        root.attributes('-topmost', True)  # In front of the browser
        path = filedialog.askdirectory(parent=root, initialdir=initial or None, title=title, mustexist=True)
    finally:
        root.destroy()
    return path or None
