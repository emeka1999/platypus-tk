import multiprocessing
multiprocessing.set_start_method("spawn", force=True)

from nicegui import ui
import bmc as bmc


fw_content = None

def update_button():
    if fw_content:
        bmc.bmc_update(username.value, password.value, bmc_ip.value, fw_content)
    else:
        ui.notify("Please upload a firmware file first.")

def ip_button():
    bmc.set_ip(bmc_ip.value, username.value, password.value)

def reset_button():
    bmc.reset_ip(username.value, password.value, bmc_ip.value)

def on_upload(event):
    global fw_content
    fw_content = event.content.read()
    ui.notify(f'Uploaded {event.name}')

with ui.column().classes('absolute-top items-center mt-20'):
    with ui.row():
        with ui.card().classes('no-shadow border-[1px] w-96 h-75'):
            username = ui.input("Username: ").classes('w-72')
            password = ui.input('Password: ').classes('w-72').props('type=password')
            bmc_ip = ui.input("BMC IP: ").classes('w-72')
    with ui.row().classes('mt-6'):
        ui.upload(on_upload=on_upload, label='BMC Firmware Upload')
    with ui.row().classes('w-full justify-around mt-8'):
        ui.button('Update BMC', on_click=update_button).classes('w-48 h-10 rounded-lg')
        ui.button('Set BMC IP', on_click=ip_button).classes('w-48 h-10 rounded-lg')
        ui.button('Reset BMC', on_click=reset_button).classes('w-48 h-10 rounded-lg')

ui.run(native=True, dark=True, title='BMC App', window_size=(500, 700), reload=False)
