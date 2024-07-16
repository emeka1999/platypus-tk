from nicegui import app, ui
import bmc as bmc



fw_content = None
flash_file = None



def update_progress(value):
    print(f"Updating progress to: {value*100}%")  # Debugging line
    if value is not None and 0 <= value <= 1:
        progress_bar.set_value(value)
    else:
        print(f"Invalid progress value: {value}")  # Debugging line



async def update_button():
    fw_path = await choose_file()
    if fw_path:
        with open(fw_path, 'rb') as fw_file:
            fw_content = fw_file.read()
        await bmc.bmc_update(username.value, password.value, bmc_ip.value, fw_content, update_progress)
    else:
        ui.notify("Please upload a firmware file first.")

    # if fw_content:
    #     await bmc.bmc_update(username.value, password.value, bmc_ip.value, fw_content, update_progress)
    # else:
    #     ui.notify("Please upload a firmware file first.")


async def ip_button():
    await bmc.set_ip(bmc_ip.value, username.value, password.value, update_progress)



def reset_button():
    bmc.reset_ip(username.value, password.value, bmc_ip.value)



async def choose_file():
    global flash_file
    files = await app.native.main_window.create_file_dialog(allow_multiple=False)
    if files: 
        flash_file = files[0]
        ui.notify(f"Selected fle: {flash_file}")
        return flash_file
    else:
        ui.notify("No file selected.")



async def flashub_button():
    flash_file = await choose_file()
    if flash_file:
        await bmc.flasher(username.value, password.value, flash_file, your_ip.value, update_progress)



def on_upload(event):
    global fw_content
    fw_content = event.content.read()
    ui.notify(f'Uploaded {event.name}')
           
ui.label('Rivan Adhikari').classes('absolute top-0 left-0 text-xs text-gray-800 p-2')
with ui.column().classes('absolute-top items-center mt-20'):
    with ui.row():
        with ui.card(align_items='center').classes('no-shadow border-[0px] w-96 h-75').style('background-color:#121212;'):
            # username = ui.input("Username: ").classes('w-72 justify-center')
            # password = ui.input('Password: ').classes('w-72').props('type=password')
            # bmc_ip = ui.input("BMC IP: ").classes('w-72')
            # your_ip = ui.input("IP (U-Boot): ").classes('w-72')

            username = ui.input(placeholder='Username').classes('w-72').props('rounded outlined dense')
            password = ui.input(placeholder='Password').classes('w-72').props('rounded outlined dense type=password')
            bmc_ip = ui.input(placeholder='BMC IP').classes('w-72').props('rounded outlined dense')
            your_ip = ui.input(placeholder='U-Boot Server IP').classes('w-72').props('rounded outlined dense')


    with ui.grid(columns=2):
        ui.button('Update BMC', on_click=update_button).classes('w-48 h-10 rounded-lg')
        ui.button('Set BMC IP', on_click=ip_button).classes('w-48 h-10 rounded-lg')
        ui.button('Reset BMC', on_click=reset_button).classes('w-48 h-10 rounded-lg')
        ui.button('Flash U-Boot', on_click=flashub_button).classes('w-48 h-10 rounded-lg')
with ui.row().classes('absolute-bottom w-full p-4'):
    progress_bar = ui.linear_progress(value=0, show_value=False).classes('w-full h-2 rounded-lg')
    progress_bar.visible = True

ui.run(native=False, dark=True, title='BMC App', window_size=(500, 800), reload=False, port=8000)


