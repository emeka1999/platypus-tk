import multiprocessing
multiprocessing.set_start_method("spawn", force=True)

from nicegui import ui

with ui.column().classes('absolute-top items-center mt-20'):
    with ui.row():
        with ui.card().classes('no-shadow border-[1px] w-96 h-75'):
            ui.input("Username: ").classes('w-72')
            ui.input('Password: ').classes('w-72')  
            ui.input("BMC IP: ").classes('w-72')
    with ui.row().classes('mt-6'):  
        ui.upload(on_upload=lambda e: ui.notify(f'Uploaded {e.name}'), label='BMC Firmware Upload') 
    with ui.row().classes('w-full justify-around mt-8'):  
        ui.button('Update BMC').classes('w-48 h-10 rounded-lg')  
        ui.button('Set BMC IP').classes('w-48 h-10 rounded-lg')
        ui.button('Reset BMC').classes('w-48 h-10 rounded-lg')



ui.run(native="True", dark='True', title='BMC App', window_size=(500, 700))

