import openai
import logging
import os

port = os.environ.get('PORT', '8080')
os.environ['STREAMLIT_SERVER_PORT'] = port
os.environ['STREAMLIT_SERVER_ADDRESS'] = '0.0.0.0'

import streamlit as st
import pymysql
import argon2
import json
import requests
import time
from PIL import Image
import io

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Set up OpenAI API key
openai.api_key = os.getenv('OPENAI_API_KEY')
client = openai

st.set_page_config(page_title="AI Assistant Solutions", layout="wide", initial_sidebar_state="expanded")

def get_db_connection():
    # Fetch database connection details from environment variables
    db_user = os.environ.get('DB_USER', 'root')
    db_pass = os.environ.get('DB_PASS', '')  # Empty string for no password
    db_name = os.environ.get('DB_NAME', 'assistant_db')  # Default database name
    db_host = os.environ.get('DB_HOST', 'localhost')  # Default to localhost
    instance_connection_name = os.environ.get('INSTANCE_CONNECTION_NAME')

    # Determine if we're running in Cloud Run with Cloud SQL
    if instance_connection_name:
        # Use Unix socket path for Cloud SQL
        unix_socket = f'/cloudsql/{instance_connection_name}'
        connection = pymysql.connect(
            user=db_user,
            password=db_pass,
            unix_socket=unix_socket,
            db=db_name,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    else:
        # Use standard TCP connection for local development
        connection = pymysql.connect(
            host=db_host,
            user=db_user,
            password=db_pass,
            db=db_name,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    return connection

def init_db():
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(255) UNIQUE,
                        password TEXT,
                        thread_id TEXT
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_assistants (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        user_id INT,
                        assistant_id TEXT,
                        name TEXT,
                        description TEXT,
                        instructions TEXT,
                        file_ids TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(id)
                    )''')
        # Check if 'file_ids' column exists
        c.execute("SHOW COLUMNS FROM user_assistants LIKE 'file_ids'")
        result = c.fetchone()
        if not result:
            c.execute("ALTER TABLE user_assistants ADD COLUMN file_ids TEXT")
    conn.commit()
    conn.close()

def reset_db():
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("DROP TABLE IF EXISTS user_assistants")
        c.execute("DROP TABLE IF EXISTS users")
    conn.commit()
    conn.close()
    init_db()

def get_assistant_files():
    try:
        assistant_files = client.files.list(purpose='assistants')
        vision_files = client.files.list(purpose='vision')

        assistant_file_dict = {file.id: file.filename for file in assistant_files}
        vision_file_dict = {file.id: file.filename for file in vision_files}

        return {
            'assistants': assistant_file_dict,
            'vision': vision_file_dict
        }
    except Exception as e:
        logging.error(f"Error retrieving files: {str(e)}")
        return {'assistants': {}, 'vision': {}}

def run_streamlit():
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'assistants' not in st.session_state:
        st.session_state.assistants = {}
    if 'selected_assistant' not in st.session_state:
        st.session_state.selected_assistant = None
    if 'thread_id' not in st.session_state:
        st.session_state.thread_id = None
    if 'uploaded_files' not in st.session_state:
        st.session_state.uploaded_files = {}
    if 'share_files' not in st.session_state:
        st.session_state.share_files = False
    if 'file_references' not in st.session_state:
        st.session_state.file_references = {}
    if 'user_id' not in st.session_state:
        st.session_state.user_id = None
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'file_info' not in st.session_state:
        st.session_state.file_info = {'assistants': {}, 'vision': {}}
    if 'pending_image_confirmation' not in st.session_state:
        st.session_state.pending_image_confirmation = None
    if 'shared_files' not in st.session_state:
        st.session_state.shared_files = set()
    if 'display_images' not in st.session_state:
        st.session_state.display_images = True
    if 'deleted_file_ids' not in st.session_state:
        st.session_state.deleted_file_ids = set()

    login_sidebar()
    st.title("AI Assistant Solutions Beta")

    if is_user_logged_in():
        st.title(f"Welcome, {st.session_state.username}")
        main_app()
    else:
        st.warning("Please log in to access the application.")

def main_app():
    sidebar = st.sidebar

    with sidebar:
        help_clicked = st.sidebar.button('New User Helpful Information')

        if help_clicked:
            show_how_to()

        st.header("Assistant Management")

        if 'assistant_id' in st.session_state and st.session_state.assistant_id:
            st.info(f"Using existing assistant for user {st.session_state.username}")
        else:
            assistant_name = st.text_input("Assistant Name")
            description = st.text_input("Description")
            instructions = st.text_input("Instructions")

        if st.button("Create Assistant"):
            response = create_assistant(st.session_state.user_id, assistant_name, description, instructions)
            if response and hasattr(response, 'id'):
                st.success(f"Assistant '{assistant_name}' created successfully!")
            else:
                st.error("Error: Unable to create assistant.")
                logging.error(f"Error: {response}")

        st.subheader("Select Assistants")
        assistant_options = list(st.session_state.assistants.keys())
        selected_assistant = st.selectbox(
            "Select assistant to use",
            options=assistant_options,
            index=0 if assistant_options else None
        )
        if selected_assistant:
            st.session_state.selected_assistant = selected_assistant

        st.session_state.share_files = st.checkbox("Share files among assistants", value=st.session_state.share_files)

        uploaded_file = st.file_uploader("Upload a file for the assistant",
                                         type=["txt", "pdf", "csv", "jpg", "jpeg", "png", "webp", "gif"])

        if uploaded_file is not None and 'uploaded_file_id' not in st.session_state:
            if st.session_state.selected_assistant:
                file_info = get_or_upload_file(uploaded_file)
                if file_info:
                    file_id = file_info['id']
                    if st.session_state.share_files:
                        # If sharing is enabled, associate the file with all assistants
                        st.session_state.shared_files.add(file_id)
                        for assistant_name, assistant in st.session_state.assistants.items():
                            if file_id not in assistant['file_ids']:
                                new_file_ids = assistant['file_ids'] + [file_id]
                                update_result = update_assistant_tool_resources(assistant['id'], new_file_ids)
                                if update_result:
                                    assistant['file_ids'] = new_file_ids
                                    update_assistant_file_ids(st.session_state.user_id, assistant['id'], new_file_ids)
                        st.success(f"File '{file_info['name']}' uploaded and shared with all assistants!")
                    else:
                        # If sharing is disabled, associate the file only with the selected assistant
                        assistant = st.session_state.assistants[st.session_state.selected_assistant]
                        if file_id not in assistant['file_ids']:
                            new_file_ids = assistant['file_ids'] + [file_id]
                            update_result = update_assistant_tool_resources(assistant['id'], new_file_ids)
                            if update_result:
                                assistant['file_ids'] = new_file_ids
                                update_assistant_file_ids(st.session_state.user_id, assistant['id'], new_file_ids)
                                st.success(
                                    f"File '{file_info['name']}' uploaded and attached to assistant '{st.session_state.selected_assistant}'!")
                            else:
                                st.error("Failed to attach file to assistant.")
                        else:
                            st.warning(f"File '{file_info['name']}' is already associated with this assistant.")
                    # Update file_references
                    if file_id not in st.session_state.file_references:
                        st.session_state.file_references[file_id] = {'count': 0,'shared': st.session_state.share_files}
                    st.session_state.file_references[file_id]['count'] += len(st.session_state.assistants) if st.session_state.share_files else 1

                    st.session_state.uploaded_file_id = file_id
                    st.rerun()
                else:
                    st.error("Error uploading file.")
            else:
                st.warning("Please select an assistant before uploading a file.")

                # Reset the uploaded_file_id flag if needed
        if st.session_state.get('uploaded_file_id'):
            del st.session_state['uploaded_file_id']

        st.subheader("Image Display Settings")
        st.session_state.display_images = st.checkbox("Display images in chat", value=st.session_state.display_images)

        # File removal logic
        if st.session_state.selected_assistant:
            assistant = st.session_state.assistants[st.session_state.selected_assistant]
            st.subheader(f"Files for {st.session_state.selected_assistant}")
            files_to_remove = []
            for file_id in assistant['file_ids']:
                file_name = st.session_state.file_info['assistants'].get(file_id) or st.session_state.file_info[
                    'vision'].get(file_id) or f"Unknown file (ID: {file_id})"
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"- {file_name}")
                with col2:
                    if st.button("Remove", key=f"remove_{file_id}"):
                        st.session_state.deleted_file_ids.add(file_id)

                        if file_id not in st.session_state.file_references:
                            st.session_state.file_references[file_id] = {'count': 0,
                                                                         'shared': file_id in st.session_state.shared_files}

                        st.session_state.file_references[file_id]['count'] -= 1
                        files_to_remove.append(file_id)

                        if st.session_state.file_references[file_id]['shared']:
                            if st.session_state.file_references[file_id]['count'] == 0:
                                for asst in st.session_state.assistants.values():
                                    if file_id in asst['file_ids']:
                                        asst['file_ids'].remove(file_id)
                                        update_assistant_tool_resources(asst['id'], asst['file_ids'])
                                        update_assistant_file_ids(st.session_state.user_id, asst['id'],
                                                                  asst['file_ids'])
                                st.session_state.shared_files.remove(file_id)
                                del st.session_state.file_references[file_id]
                                for purpose in ['assistants', 'vision']:
                                    if file_id in st.session_state.file_info[purpose]:
                                        del st.session_state.file_info[purpose][file_id]
                                st.success(f"Shared file '{file_name}' removed from all assistants.")
                            else:
                                st.success(
                                    f"Shared file '{file_name}' removed from this assistant. Still in use by other assistants.")
                        else:
                            st.success(f"Non-shared file '{file_name}' removed from the assistant.")
                            del st.session_state.file_references[file_id]
                            for purpose in ['assistants', 'vision']:
                                if file_id in st.session_state.file_info[purpose]:
                                    del st.session_state.file_info[purpose][file_id]

            if files_to_remove:
                st.session_state.deleted_file_ids.update(files_to_remove)
                assistant['file_ids'] = [fid for fid in assistant['file_ids'] if fid not in files_to_remove]
                update_assistant_tool_resources(assistant['id'], assistant['file_ids'])
                update_assistant_file_ids(st.session_state.user_id, assistant['id'], assistant['file_ids'])
                st.rerun()

            if not assistant['file_ids']:
                st.write("No files associated with this assistant.")

        assistant_to_delete = st.selectbox("Select assistant to delete",
                                           options=[''] + list(st.session_state.assistants.keys()))
        if st.button("Delete Assistant") and assistant_to_delete:
            assistant_id = st.session_state.assistants[assistant_to_delete]['id']
            response = delete_assistant(assistant_id)
            if response:
                del st.session_state.assistants[assistant_to_delete]
                remove_assistant_from_db(assistant_id)
                st.success(f"Assistant '{assistant_to_delete}' deleted successfully!")
                if st.session_state.selected_assistant == assistant_to_delete:
                    st.session_state.selected_assistant = None
                st.rerun()
            else:
                st.error("Error: Unable to delete assistant.")
                logging.error(f"Error deleting assistant: {response}")

        st.subheader("Thread Management")
        if not st.session_state.thread_id:
            if st.button("Create New Thread"):
                response = create_thread()
                if response and hasattr(response, 'id'):
                    st.session_state.thread_id = response.id
                    update_user_thread_id(st.session_state.user_id, response.id)
                    st.success("New thread created successfully!")
                else:
                    st.error("Error: Unable to create thread.")
                    logging.error(f"Error: {response}")
        else:
            st.success(f"Current thread ID: {st.session_state.thread_id}")
            if st.button("Start New Thread"):
                response = create_thread()
                if response and hasattr(response, 'id'):
                    st.session_state.thread_id = response.id
                    update_user_thread_id(st.session_state.user_id, response.id)
                    st.success("New thread created successfully!")
                else:
                    st.error("Error: Unable to create thread.")
                    logging.error(f"Error: {response}")

        st.subheader("User Account Management")
        if st.button("Delete My Account"):
            delete_user_account(st.session_state.username)
            for key in ['user_id', 'thread_id', 'username', 'assistants', 'selected_assistant']:
                if key in st.session_state:
                    del st.session_state[key]
            st.success("Your account has been deleted.")
            st.rerun()

    chat_container = st.container()

    with chat_container:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

    if st.button("Clear Chat History"):
        st.session_state.messages = []
        st.rerun()

    user_input_container = st.container()

    with user_input_container:
        user_message = st.chat_input("Type your message here...")

        if user_message:
            with chat_container.chat_message("user"):
                st.markdown(user_message)

            if st.session_state.selected_assistant:
                run_message_stream(user_message, st.session_state.selected_assistant, chat_container)
            else:
                st.warning("Please select an assistant.")

    st.markdown(
        """
        <script>
        var chatContainer = document.querySelector('section.main');
        chatContainer.scrollTop = chatContainer.scrollHeight;
        </script>
        """,
        unsafe_allow_html=True
    )

def display_or_download_image(file_id, filename="image.png"):
    if not st.session_state.display_images:
        return

    logger.debug(f"Attempting to display or download image with file_id: {file_id}")
    try:
        api_url = f"https://api.openai.com/v1/files/{file_id}/content"
        headers = {"Authorization": f"Bearer {openai.api_key}"}
        logger.debug(f"Sending GET request to: {api_url}")
        response = requests.get(api_url, headers=headers)
        logger.debug(f"Response status code: {response.status_code}")

        if response.status_code == 200:
            file_content = response.content
            logger.debug(f"Successfully retrieved file content for file_id: {file_id}")

            # Open the image using Pillow
            image = Image.open(io.BytesIO(file_content))

            # Correct orientation if needed
            if hasattr(image, '_getexif'):  # Only present in JPEGs
                exif = image._getexif()
                if exif:
                    orientation = exif.get(274, 1)  # 274 is the orientation tag
                    if orientation == 3:
                        image = image.rotate(180, expand=True)
                    elif orientation == 6:
                        image = image.rotate(270, expand=True)
                    elif orientation == 8:
                        image = image.rotate(90, expand=True)

            # Convert the image back to bytes
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_byte_arr = img_byte_arr.getvalue()

            # Display the corrected image
            st.image(img_byte_arr, caption=filename, use_column_width=True)
            logger.debug(f"Displayed image for file_id: {file_id}")

            # Create a unique key for each download button
            unique_key = f"download_button_{file_id}_{int(time.time())}"

            download_button = st.download_button(
                label="Download image",
                data=img_byte_arr,
                file_name=filename,
                mime="image/png",
                key=unique_key
            )
            logger.debug(f"Created download button for file_id: {file_id} with key: {unique_key}")

            if download_button:
                logger.debug(f"Download button clicked for file_id: {file_id}")
        elif response.status_code == 404:
            logger.error(f"File not found. File ID: {file_id}")
            st.error("File not found. Please check the file ID or upload a new file.")
            return
        else:
            logger.error(f"Failed to retrieve image. Status code: {response.status_code}, File ID: {file_id}")
            st.error(f"Could not retrieve the image. Status code: {response.status_code}")
    except Exception as e:
        logger.exception(f"Error in display_or_download_image for file_id {file_id}: {str(e)}")
        st.error("Failed to display or download the image. Please try again later.")

def run_message_stream(user_message, selected_assistant, chat_container):
    try:
        thread_id = st.session_state.thread_id
        assistant_id = st.session_state.assistants[selected_assistant]['id']

        logging.info(f"Starting run_message_stream with thread_id: {thread_id}, assistant_id: {assistant_id}")
        logging.info(f"display_images setting: {st.session_state.display_images}")

        message_content = [{"type": "text", "text": user_message}]
        image_file_ids = []
        file_ids_for_code_interpreter = []

        logging.info(f"User message: {user_message}")
        logging.info(f"Assistant file_ids: {st.session_state.assistants[selected_assistant]['file_ids']}")

        # Identify file IDs for vision and code interpreter purposes
        for file_id in st.session_state.assistants[selected_assistant]['file_ids']:
            logging.info(f"Checking file_id: {file_id}")
            if check_file_exists_on_server(file_id):
                if file_id in st.session_state.file_info['vision']:
                    image_file_ids.append(file_id)
                    logging.info(f"Added {file_id} to image_file_ids")
                elif file_id in st.session_state.file_info['assistants']:
                    file_ids_for_code_interpreter.append(file_id)
                    logging.info(f"Added {file_id} to file_ids_for_code_interpreter")
            else:
                logging.warning(f"File with ID {file_id} is marked as deleted. Skipping.")

        logging.info(f"image_file_ids: {image_file_ids}")
        logging.info(f"file_ids_for_code_interpreter: {file_ids_for_code_interpreter}")

        # Update assistant with file_ids for code interpreter
        if file_ids_for_code_interpreter:
            logging.info("Updating assistant with code interpreter file IDs")
            updated_assistant = update_assistant_tool_resources(assistant_id, file_ids_for_code_interpreter)
            if updated_assistant is None:
                logging.error("Failed to update assistant with new file resources.")
                st.error("Failed to update assistant with new file resources.")
                return
            logging.info("Assistant updated successfully")

        # Add image file IDs to the message content
        for image_file_id in image_file_ids:
            message_content.append({"type": "image_file", "image_file": {"file_id": image_file_id}})

        logging.info(f"Final message_content: {message_content}")

        # Create the message in the thread
        created_message = client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=message_content
        )
        logging.info(f"Created message: {created_message}")

        # Stream the assistant's response
        with chat_container.chat_message("assistant"):
            st.write(f"Response from {selected_assistant}:")
            with client.beta.threads.runs.stream(
                    thread_id=thread_id,
                    assistant_id=assistant_id,
            ) as stream:
                st.write_stream(stream.text_deltas)
                stream.until_done()

        logging.info("Assistant response streaming completed")

        # Check for image output in the response
        logging.info(f"Checking for image output. display_images: {st.session_state.display_images}")
        if st.session_state.display_images:
            logging.info("Display images is True, proceeding to check for image output")
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            logging.info(f"Retrieved {len(messages.data)} messages from the thread")
            displayed_files = set()  # Keep track of displayed files
            for message in messages.data:
                for content in message.content:
                    logging.info(f"Content type: {content.type}")
                    if content.type == 'image_file':
                        file_id = content.image_file.file_id
                        logging.info(f"Found image file with ID: {file_id}")
                        if file_id not in displayed_files and check_file_exists(file_id):
                            logging.info(f"Displaying image: {file_id}")
                            display_or_download_image(file_id)
                            displayed_files.add(file_id)  # Mark as displayed
                        elif file_id in st.session_state.deleted_file_ids:
                            logging.warning(f"File with ID {file_id} has been removed and cannot be displayed.")
                        else:
                            logging.warning(f"File with ID {file_id} has already been displayed.")
        else:
            logging.info("Display images is False, skipping image output check")

        logging.info("run_message_stream completed successfully")
    except Exception as e:
        logging.error(f"Error during message stream: {str(e)}", exc_info=True)
        st.error("An error occurred during the message stream. Please try again.")

def check_file_exists(file_id):
    if file_id in st.session_state.deleted_file_ids:
        return False
    return True  # Assume the file exists if it's not in deleted_file_ids

def remove_assistant_from_db(assistant_id):
    try:
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute("DELETE FROM user_assistants WHERE assistant_id = %s", (assistant_id,))
        conn.commit()
        conn.close()
        logging.info(f"Assistant ID {assistant_id} removed from database")
    except Exception as e:
        logging.error(f"Error removing assistant ID {assistant_id} from database: {str(e)}")

def create_assistant(user_id, assistant_name, description, instructions):
    try:
        tools = [{"type": "code_interpreter"}]
        response = client.beta.assistants.create(
            name=assistant_name,
            description=description,
            instructions=instructions,
            model="gpt-4o",
            tools=tools
        )
        assistant_id = response.id

        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO user_assistants (user_id, assistant_id, name, description, instructions, file_ids) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, assistant_id, assistant_name, description, instructions, json.dumps([])))
        conn.commit()
        conn.close()

        st.session_state.assistants[assistant_name] = {
            'id': assistant_id,
            'description': description,
            'instructions': instructions,
            'file_ids': []
        }
        st.session_state.selected_assistant = assistant_name
        return response
    except Exception as e:
        logging.error(f"Error creating assistant: {str(e)}")
        st.error("Failed to create the assistant. Please check the input values and try again.")
        return None

def get_or_upload_file(file):
    try:
        file_name = file.name
        file_extension = file.name.split('.')[-1].lower()

        if file_extension in ['jpg', 'jpeg', 'png', 'webp', 'gif']:
            purpose = 'vision'
        else:
            purpose = 'assistants'

        # Upload the file
        file_response = client.files.create(file=file, purpose=purpose)
        file_info = {
            'id': file_response.id,
            'name': file_name,
            'purpose': purpose
        }
        # Remove from deleted_file_ids if it was there
        if file_response.id in st.session_state.deleted_file_ids:
            st.session_state.deleted_file_ids.remove(file_response.id)
        # Update session state
        if purpose not in st.session_state.file_info:
            st.session_state.file_info[purpose] = {}
        st.session_state.file_info[purpose][file_response.id] = file_name

        return file_info
    except Exception as e:
        logging.error(f"Error uploading file: {str(e)}")
        return None

def update_assistant_file_ids(user_id, assistant_id, file_ids):
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("UPDATE user_assistants SET file_ids = %s WHERE user_id = %s AND assistant_id = %s",
                  (json.dumps(file_ids), user_id, assistant_id))
    conn.commit()
    conn.close()

def check_file_exists_on_server(file_id):
    try:
        api_url = f"https://api.openai.com/v1/files/{file_id}"
        headers = {"Authorization": f"Bearer {openai.api_key}"}
        response = requests.get(api_url, headers=headers)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Error checking file existence on server: {str(e)}")
        return False

def update_assistant_tool_resources(assistant_id, file_ids):
    try:
        current_assistant = client.beta.assistants.retrieve(assistant_id)
        unique_file_ids = list(set(file_ids))
        updated_assistant = client.beta.assistants.update(
            assistant_id=assistant_id,
            tools=current_assistant.tools,
            tool_resources={
                "code_interpreter": {
                    "file_ids": unique_file_ids
                }
            }
        )
        logging.info(f"Updated assistant: {updated_assistant}")
        return updated_assistant
    except Exception as e:
        logging.error(f"Error updating assistant tool resources: {str(e)}")
        return None

def delete_assistant(assistant_id):
    try:
        response = client.beta.assistants.delete(assistant_id=assistant_id)
        logging.debug("Assistant deleted successfully")
        return response
    except Exception as e:
        logging.error(f"Error deleting assistant: {str(e)}")
        return None

def delete_file_from_openai(file_id):
    try:
        response = client.files.delete(file_id)
        logging.info(f"File {file_id} deleted from OpenAI. Response: {response}")

        return True
    except Exception as e:
        logging.error(f"Error deleting file {file_id} from OpenAI: {str(e)}")
        return False
def is_user_logged_in():
    return st.session_state.user_id is not None

def hash_password(password):
    hasher = argon2.PasswordHasher()
    return hasher.hash(password.encode('utf-8'))

def verify_password(password, hashed_password):
    hasher = argon2.PasswordHasher()
    try:
        hasher.verify(hashed_password, password.encode('utf-8'))
        return True
    except argon2.exceptions.VerifyMismatchError:
        return False

def create_user(username, password):
    hashed_password = hash_password(password)
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("INSERT INTO users (username, password) VALUES (%s, %s)",
                  (username, hashed_password))
        user_id = c.lastrowid
    conn.commit()
    conn.close()
    return user_id

def verify_user(username, password):
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("""
            SELECT u.id, u.password, u.thread_id, ua.assistant_id
            FROM users u
            LEFT JOIN user_assistants ua ON u.id = ua.user_id
            WHERE u.username = %s
        """, (username,))
        user = c.fetchone()
    conn.close()
    if user:
        stored_password = user['password']
        if verify_password(password, stored_password):
            return user['id'], user['thread_id'] or None, user.get('assistant_id')  # user_id, thread_id, assistant_id
    return None, None, None

def delete_user_account(username):
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("DELETE FROM users WHERE username = %s", (username,))
        c.execute("DELETE FROM user_assistants WHERE user_id = (SELECT id FROM users WHERE username = %s)", (username,))
    conn.commit()
    conn.close()
    logging.info(f"User account for {username} has been deleted.")

def show_how_to():
    st.title("How to Use the App")

    st.subheader("About the application")
    st.write("""
    This is a beta version with Known issues:   No password recovery; for a forgotten password or username - you need to create a new account.  Create a new thread with the first assistant you build. 
    The description can be simple. The instructions are more important and extensive instructions can be pasted into the instruction block.
    starter examples below. After file upload always click the "x" next to the file before you start the dialog with the assistant. Up to 20 files can be uploaded.
    Each thread is a "memory" so the assistant will remember your conversation until you create a new thread, even if you logout. Uploaded files are deleted when you create a new thread, but you must remove all files from all assistants first. unclick the view images if you do not want to keep seeing the image after the chat.
    The share files click is problematic for more than three assistants, so it is best to leave it unclicked. A very useful tip is that you can upload PNG files that are screen saves or snips for the assistant
    to interpret and use; this is a very powerful and useful ability.  Phone photos can also be uploaded. Please delete all of your 
    Assistants BEFORE deleting your account (to prevent a database error).
    """)

    st.subheader("Creating an Assistant")
    st.write("""
    To create a new assistant:
    1. Go to the **Assistant Management** section in the sidebar.
    2. Enter a **Name**, **Description**, and **Instructions** for your assistant.
    3. Click **Create Assistant**.
    4. Your assistant will appear in the **Select Assistants** dropdown.
    """)

    st.subheader("Sample Instructions")
    sample_instructions = {
        "Sample Descriptions": "You are a helpful and friendly AI assistant,You are an Expert Writer , Expert Poet, Expert Historian, etc ... AI Assistant.",
        "Math Tutor": "You are an Expert Calculus Tutor, you are friendly and helpful, you can solve problems that the user uploads as image files, you give step by step explanations for your answers and you show your work.",
        "Image Analysis Assistant": "You are an assistant specialized in detailed image analysis. Help the user interpret images and provide insights.",
        "Data Helper": "You can make graphs and tables for the user.  Provide analysis of any data images or text , remind the user to click view images to see and download graphs and tables.",
        "Coding Helper": "Assist the user with writing and debugging Python code. Explain concepts clearly and provide code examples."
    }

    for name, instruction in sample_instructions.items():
        with st.expander(name):
            st.write(instruction)

    # Add a 'Close Help' button
    if st.button('Close Help'):
        st.rerun()

def login_sidebar():
    st.sidebar.title("User Login")
    username = st.sidebar.text_input("Username")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Login"):
        user_id, thread_id, assistant_id = verify_user(username, password)
        if user_id:
            st.session_state.user_id = user_id
            st.session_state.thread_id = thread_id
            st.session_state.username = username
            logging.info(f"User {username} logged in. Thread ID: {thread_id}")
            st.sidebar.success(f"Logged in as {username}")

            st.session_state.assistants = load_user_assistants(user_id)
            if st.session_state.assistants:
                st.sidebar.info(f"Loaded {len(st.session_state.assistants)} assistants for user {username}")
            else:
                st.sidebar.warning("No existing assistants found. Please create a new assistant.")

            st.session_state.file_info = get_assistant_files()

            if thread_id:
                st.sidebar.info(f"Loaded existing thread: {thread_id}")
            else:
                st.sidebar.warning("No existing thread found. Please create a new thread to start a conversation.")
            st.rerun()
        else:
            st.sidebar.error("Invalid username or password")

    if st.sidebar.button("Create New Account"):
        if username and password:
            try:
                user_id = create_user(username, password)
                st.session_state.user_id = user_id
                st.session_state.username = username
                st.session_state.assistants = {}
                st.sidebar.success(f"Account created for {username}")
                st.rerun()
            except Exception as e:
                st.sidebar.error("Username already exists or error occurred")
                logging.error(f"Error creating user: {str(e)}")
        else:
            st.sidebar.error("Please enter a username and password")

    if st.session_state.user_id is not None:
        st.sidebar.write(f"Logged in as: {st.session_state.username}")
        if st.sidebar.button("Logout"):
            if st.session_state.thread_id:
                update_user_thread_id(st.session_state.user_id, st.session_state.thread_id)

            for key in ['user_id', 'thread_id', 'username', 'assistants', 'selected_assistant']:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

def load_user_assistants(user_id):
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("SELECT assistant_id, name, description, instructions, file_ids FROM user_assistants WHERE user_id = %s",
                  (user_id,))
        rows = c.fetchall()
    conn.close()
    assistants = {}
    for row in rows:
        assistant_id = row['assistant_id']
        name = row['name']
        description = row['description']
        instructions = row['instructions']
        file_ids = json.loads(row['file_ids'])

        # Sync with available files
        available_files = get_assistant_files()
        synced_file_ids = [fid for fid in file_ids if
                           fid in available_files['assistants'] or fid in available_files['vision']]

        assistants[name] = {
            'id': assistant_id,
            'description': description,
            'instructions': instructions,
            'file_ids': synced_file_ids
        }

        # Update the database if file_ids changed
        if synced_file_ids != file_ids:
            conn = get_db_connection()
            with conn.cursor() as c:
                c.execute("UPDATE user_assistants SET file_ids = %s WHERE assistant_id = %s",
                          (json.dumps(synced_file_ids), assistant_id))
            conn.commit()
            conn.close()
    return assistants

def update_user_thread_id(user_id, thread_id):
    conn = get_db_connection()
    with conn.cursor() as c:
        c.execute("UPDATE users SET thread_id = %s WHERE id = %s", (thread_id, user_id))
    conn.commit()
    conn.close()
    logging.info(f"Updated thread_id for user {user_id}: {thread_id}")

def create_thread():
    try:
        response = client.beta.threads.create()
        logging.debug(f"Thread created with ID: {response.id}")

        for file_id in st.session_state.deleted_file_ids:
            delete_file_from_openai(file_id)

        return response
    except Exception as e:
        logging.error(f"Error creating thread (did you forget to remove files from assistants?): {str(e)}")
        return None

if __name__ == '__main__':
    init_db()
    run_streamlit()
