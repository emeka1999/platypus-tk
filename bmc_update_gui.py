import tkinter as tk
from tkinter import filedialog
import BMC_update_script as bmc
import time

def browse_file():
    filepath = filedialog.askopenfilename()
    if filepath:
        entry_file.config(state='normal')
        entry_file.delete(0, tk.END)
        entry_file.insert(0, filepath)

def ip_button():
    bmc_ip = entry_ip.get()
    bmc_user = entry_user.get()
    bmc_pass = entry_pass.get()
    bmc.set_ip(bmc_ip, bmc_user, bmc_pass)
    time.sleep(5)


def fw_update():
    bmc_ip = entry_ip.get()
    bmc_user = entry_user.get()
    bmc_pass = entry_pass.get()
    fw_path = entry_file.get()

    if not all([bmc_user, bmc_pass, bmc_ip, fw_path]):
        label_output.config(text="All fields are required.", fg="red")
        return
    
    bmc.bmc_update(bmc_user, bmc_pass, bmc_ip, fw_path)

def reset_bmc_ip():
    bmc_user = entry_user.get()
    bmc_pass = entry_pass.get()
    bmc_ip = entry_ip.get()

    if not all([bmc_user, bmc_pass, bmc_ip]):
        label_output.config(text = "All fields are required.", fg = "red")
        return
    bmc.reset_bmc(bmc_user, bmc_pass, bmc_ip)

# GUI Setup
win = tk.Tk()
win.title("BMC Firmware Update")

label_ip = tk.Label(win, text="BMC IP:")
label_ip.grid(row=0, column=0, padx=5, pady=5)
entry_ip = tk.Entry(win)
entry_ip.grid(row=0, column=1, padx=5, pady=5)

label_user = tk.Label(win, text="Username:")
label_user.grid(row=1, column=0, padx=5, pady=5)
entry_user = tk.Entry(win)
entry_user.grid(row=1, column=1, padx=5, pady=5)

label_pass = tk.Label(win, text="Password:")
label_pass.grid(row=2, column=0, padx=5, pady=5)
entry_pass = tk.Entry(win, show="*")
entry_pass.grid(row=2, column=1, padx=5, pady=5)

label_file = tk.Label(win, text="Firmware File:")
label_file.grid(row=3, column=0, padx=5, pady=5)
entry_file = tk.Entry(win, state="readonly")
entry_file.grid(row=3, column=1, padx=5, pady=5)

button_browse = tk.Button(win, text="Browse", command=browse_file)
button_browse.grid(row=3, column=2, padx=5, pady=5)

button_update = tk.Button(win, text="Update Firmware", command=fw_update)
button_update.grid(row=4, column=1, padx=5, pady=5)

label_output = tk.Label(win, text="", wraplength=400, justify="left")
label_output.grid(row=5, column=0, columnspan=3, padx=10, pady=10)

set_ip_button = tk.Button(win, text="Set BMC IP", command=ip_button)
set_ip_button.grid(row=4, column=2, padx=20, pady=5)

button_reset_ip = tk.Button(win, text = "Reset BMC IP", command = reset_bmc_ip)
button_reset_ip.grid(row = 2, column = 2, padx = 5, pady = 5)

win.mainloop()
