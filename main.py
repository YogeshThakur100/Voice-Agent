import asyncio
import json
import os
import time
import base64
from typing import Dict, Optional
import websockets
import websockets.client
import aiohttp
from fastapi import FastAPI, WebSocket, Request, Form 
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
import logging
import urllib.parse
from twilio.rest import Client
from utlity import AI_Integration
import uuid

# Load environment variables from .env file
load_dotenv()

# Retrieve the OpenAI API key from environment variables
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

if not OPENAI_API_KEY:
    print('Missing OpenAI API key. Please set it in the .env file.')
    exit(1)

# Initialize FastAPI
app = FastAPI()

# Constants


VOICE = 'alloy'
# PORT = int(os.getenv('PORT', 5050))
# WEBHOOK_URL = "<input your webhook URL here>"

# Session management
sessions: Dict[str, Dict] = {}

# List of Event Types to log to the console
LOG_EVENT_TYPES = [
    'response.content.done',
    'rate_limits.updated',
    'response.done',
    'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started',
    'session.created',
    'response.text.done',
    'conversation.item.input_audio_transcription.completed'
]

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

global_context = {}
session_data = {}


@app.post('/call-voice-agent')
async def call_voice_agent(request : Request):
    try:
        data = await request.json()
        company = data.get('company_name')
        role = data.get('role')
        candidate = data.get('candidate_name')
        local_url = os.getenv('local_url')

        job_summery = data.get('job_summery')

        try:
            # questions = AI_Integration.makeQuestion(job_summery)
            questions = """
            1. What is your experience with integrating APIs like OpenAI and Hugging Face into applications?
            2. Can you describe your proficiency in Python, specifically in the context of building AI-powered solutions?
            3. How have you utilized LangChain or vector databases in your previous projects?
            4. Have you implemented features such as summarization and question answering using large language models?
            """
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={
                    "success" : False,
                    "message" : "Error in getting the questions from job summery",
                    "data" : {}
                }
            )
        
        print('questions --->' , questions)

        # questions_str = "|".join(questions)


        params = {
            "company": company,
            "candidate": candidate,
            "questions": questions,
            "role" : role
        }

        print('params ---->' , params)

        query_string = urllib.parse.urlencode(params)

        account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        account_auth = os.getenv('TWILIO_AUTH_TOKEN')

        client = Client(account_sid , account_auth)

        call = client.calls.create(
            from_='+15513483782',
            to=data.get('candidate_phone_number'),
            status_callback=f"{local_url}/events",
            status_callback_event=["queued", "initiated", "ringing", "in-progress", "completed"],
            status_callback_method="POST",
            url=f"{local_url}/incoming-call?{query_string}"
        )

        print(call.sid)
        return JSONResponse(
            status_code=200,
            content={
                "success" : True,
                "message" : "Successfully called voice agent",
                "data" : {}
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success" : False,
                "message" : str(e),
                "data" : {}
            }
        )
    
@app.post("/events")
async def call_status(request: Request):
    form_data = await request.form()
    print("Form Data -----> : " , form_data)
    
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    call_duration = form_data.get("CallDuration")  # Only present on 'completed'

    print("📞 Call SID:", call_sid)
    print("📊 Status:", call_status)
    print("⏱ Duration:", call_duration)

    if call_status == "no-answer":
        print("❌ Call not picked")
    elif call_status == "canceled":
        print("🚫 Call canceled before ringing")
    elif call_status == "busy":
        print("📞 Line was busy")
    elif call_status == "completed":
        print(f"✅ Call completed. Duration: {call_duration} seconds")

    return PlainTextResponse("OK", status_code=200)


# Root Route
@app.get('/')
async def root():
    return {"message": "Twilio Media Stream Server is running!"}

# Route for Twilio to handle incoming and outgoing calls
@app.api_route('/incoming-call', methods=['GET', 'POST'])
async def incoming_call(request: Request):
    logger.info('Incoming call')
    
    host = request.headers.get('host', 'localhost')
    data = request.query_params

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say>This is an AI-generated screening call from {data.get('company')} . Please ensure you are in a quiet environment before we begin . Kindly wait for the AI to complete each question, and then provide your response . When you are ready, just say 'Hello' to start.</Say>
        <Connect>
            <Stream url="wss://{host}/media-stream">
            <Parameter name="company" value="{data.get('company')}"/>
            <Parameter name="role" value="{data.get('role')}"/>
            <Parameter name="candidate" value="{data.get('candidate')}"/>
            <Parameter name="questions" value="{','.join(data.get('questions', []))}"/>
            </Stream>
        </Connect>
    </Response>"""

    return PlainTextResponse(content=twiml_response, media_type='text/xml')

# WebSocket route for media-stream
@app.websocket('/media-stream')
async def media_stream_websocket(websocket: WebSocket):
    await websocket.accept()
    logger.info('Client connected')

    session_id = websocket.headers.get('x-twilio-call-sid', f'session_{int(time.time() * 1000)}')
    session = sessions.get(session_id, {'transcript': '', 'stream_sid': None})
    sessions[session_id] = session

    # Connect to OpenAI WebSocket
    openai_ws = None
    try:
        # Simple connection approach that should work with most websockets versions
        headers = {
            'Authorization': f'Bearer {OPENAI_API_KEY}',
            'OpenAI-Beta': 'realtime=v1'
        }
        
        # Try the most common connection method first
        try:
            openai_ws = await websockets.connect(
                'wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview-2024-12-17',
                extra_headers=headers
            )
        except Exception as e:
            logger.error(f'First connection attempt failed: {e}')
            # Fallback method
            openai_ws = await websockets.client.connect(
                'wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview-2024-12-17',
                extra_headers=headers
            )

        async def send_session_update(SYSTEM_MESSAGE : str):
            session_update = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad"},
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "voice": VOICE,
                    "instructions": SYSTEM_MESSAGE,
                    "modalities": ["text", "audio"],
                    "temperature": 0.8,
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    }
                }
            }

            logger.info(f'Sending session update: {json.dumps(session_update)}')
            await openai_ws.send(json.dumps(session_update))

        # Send session update after connection
        # await asyncio.sleep(0.25)
        # await send_session_update()

        async def handle_openai_messages():
            try:
                async for message in openai_ws:
                    try:
                        response = json.loads(message)

                        if response.get('type') in LOG_EVENT_TYPES:
                            logger.info(f"Received event: {response.get('type')}, {response}")

                        # User message transcription handling
                        if response.get('type') == 'conversation.item.input_audio_transcription.completed':
                            user_message = response.get('transcript', '').strip()
                            session['transcript'] += f"User: {user_message}\n"
                            logger.info(f"User ({session_id}): {user_message}")

                        # Agent message handling
                        if response.get('type') == 'response.done':
                            agent_message = 'Agent message not found'
                            if response.get('response', {}).get('output'):
                                for output in response['response']['output']:
                                    content = output.get('content', [])
                                    for c in content:
                                        if c.get('transcript'):
                                            agent_message = c['transcript']
                                            break
                                    if agent_message != 'Agent message not found':
                                        break
                            session['transcript'] += f"Agent: {agent_message}\n"
                            logger.info(f"Agent ({session_id}): {agent_message}")

                        if response.get('type') == 'session.updated':
                            logger.info(f'Session updated successfully: {response}')

                        if response.get('type') == 'response.audio.delta' and response.get('delta'):
                            audio_delta = {
                                "event": "media",
                                "streamSid": session['stream_sid'],
                                "media": {
                                    "payload": base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                                }
                            }
                            await websocket.send_text(json.dumps(audio_delta))

                    except json.JSONDecodeError as e:
                        logger.error(f'Error parsing OpenAI message: {e}, Raw message: {message}')
                    except Exception as e:
                        logger.error(f'Error processing OpenAI message: {e}, Raw message: {message}')

            except websockets.exceptions.ConnectionClosed:
                logger.info('OpenAI WebSocket connection closed')
            except Exception as e:
                logger.error(f'Error in OpenAI WebSocket handler: {e}')

        # Start handling OpenAI messages
        openai_task = asyncio.create_task(handle_openai_messages())

        # Handle incoming messages from Twilio
        try:
            while True:
                message = await websocket.receive_text()
                try:
                    data = json.loads(message)

                    if data.get('event') == 'media':
                        if openai_ws and not openai_ws.closed:
                            audio_append = {
                                "type": "input_audio_buffer.append",
                                "audio": data['media']['payload']
                            }
                            await openai_ws.send(json.dumps(audio_append))

                    elif data.get('event') == 'start':
                        session['stream_sid'] = data['start']['streamSid']

                        params = data["start"].get("customParameters", {})
                        company = params.get("company", "")
                        role = params.get("role", "")
                        candidate = params.get("candidate", "")
                        questions = params.get("questions", "").split(",")

                        SYSTEM_MESSAGE = f"""
                        You are company automated recruitment voice assistant. Your job is to conduct screening interviews with candidates.

                        ### Objective:
                        - Greet the candidate
                        - Verify identity
                        - Conduct pre-screening questions
                        - Handle responses appropriately
                        - Keep responses SHORT and conversational for voice
                        - Always repeat the questions again if candidate didn't understand properly or respond with something else which is not related to that particular question
                        - Also remember to verify the answer with the current question if the answer is irrelevant out of the question content then ask the question again 

                        ### Flow:
                        1. Greet: "Hello, am I speaking with {candidate}"
                        2. If yes, proceed to verification
                        3. Verify: "We received your application for {role} at {company}. Is now a good time for a quick 2-3 minute screening?"
                        4. If yes, proceed with questions one by one
                        5. Ask questions:
                        Before moving to the next question , wait for the user reply
                            - Can you please tell me your total years of professional experience?
                            - What is your current CTC (Cost to Company)?
                            - What is your expected CTC for this opportunity?
                            - What is your current notice period, and are you open to negotiating it if required?
                            - Which city are you currently based in?
                            - Could you share your key skills and relevant experience for this role?
                            - {questions}
                        6. Ask: "Do you have any questions or concerns?"
                        7. End: "Thank you for your time. Our team will follow up with you soon."

                        ### Rules:
                        - Keep responses SHORT (1-2 sentences max)
                        - Ask ONE question at a time
                        - Wait for response before next question
                        - Be conversational and natural
                        - If you don't understand, ask for clarification
                        - Always end with a question or clear next step

                        And if the user asks about the information like company details or interview dates and all, use the tools provided to you.
                        """

                        logger.info(f"Stream started for {candidate}, role={role}, company={company}, questions={questions}")
                        logger.info(f'Incoming stream has started {session["stream_sid"]}')

                        await send_session_update(SYSTEM_MESSAGE)

                    else:
                        logger.info(f'Received non-media event: {data.get("event")}')

                except json.JSONDecodeError as e:
                    logger.error(f'Error parsing message: {e}, Message: {message}')

        except Exception as e:
            logger.error(f'Error in WebSocket message handling: {e}')

    except Exception as e:
        logger.error(f'Error connecting to OpenAI: {e}')

    finally:
        # Handle connection close and log transcript
        transcript_text = session['transcript']
        if isinstance(transcript_text, list):
            transcript_text = '\n'.join(map(str, transcript_text))
        else:
            transcript_text = str(transcript_text).replace('\\n', '\n')

        print('session_trscript_text ---->' , session['transcript'])
        try:
            with open('conversation_History.txt', 'w', encoding='utf-8') as f:
                f.write(transcript_text)
            logger.info('conversation_History.txt created and populated successfully.')
        except Exception as e:
            logger.error(f'Failed to write conversation_History.txt: {e}')

        if openai_ws and not openai_ws.closed:
            await openai_ws.close()

        logger.info(f'Client disconnected ({session_id}).')
        logger.info('Full Transcript:')
        logger.info(session['transcript'])

        await process_transcript_and_send(session['transcript'], session_id)

        # Clean up the session
        sessions.pop(session_id, None)

# Function to make ChatGPT API completion call
async def make_chatgpt_completion(transcript: str):
    logger.info('Starting Recruiting ChatGPT API call...')
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {OPENAI_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": "gpt-4o-mini-2024-07-18",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a recruiting assistant. Extract candidate information from the conversation transcript and return it as a JSON object with the following structure: {\"total_experience\": \"integer\", \"ctc\": \"integer\", \"ectc\": \"integer\", \"notice_period\": \"string\", \"city\": \"string\", \"communication\": \"string\", \"communication_rating\": \"integer\" , \"communication_rating\": \"List\" , \"Applicant Summary\": \"string\"}. Only return the JSON object, no additional text."
                        },
                        {
                            "role": "user",
                            "content": transcript
                        }
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "candidate_details_extraction",
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "total_experience": {"type": "integer", "description": "total_experience in numbers given by candidate"},
                                    "ctc": {"type": "integer", "description": "candidate current cost to company in numbers like if user tell 5Lakh then convert it in to rupess 500000 which must you return"},
                                    "ectc": {"type": "integer", "description": "candidate expected cost to company in numbers like if user tell 5Lakh then convert it in to rupess 500000 which must you return"},
                                    "notice_period": {"type": "string", "description": "[IMMEDIATE , 1 WEEK , 15 DAYS , 1 MONTH , 2 MONTHS , 3 MONTHS] give me the response from the list only based on the candidate answer"},
                                    "city": {"type": "string", "description": "current city of the employee given by candidate"},
                                    "communication": {"type": "string", "description": "[POOR , GOOD , MODERATE , EXCELLENT ] give me the communication tag based on the conversation"},
                                    "communication_rating": {"type": "integer", "description": "based on the conversation , rate the communication skill out of 10 of the candidate must be integer not float"},
                                    "experience_skillset": {"type": "string", "description": "[] Provide the list of the communication_rating of technology the candidate knows like [python , react.js]"},
                                    "applicant_summary": {"type": "string", "description": "state based on the answer given by the candiate , does it will be fit for the job or not describe this in 3-4sentences good things or bad things about the candidate"},
                                    "status" : {"type": "string", "description": "based on the answer provided by the candidate give the status True if selected and False if rejected"}
                                },
                                "required": ["total_experience", "ctc", "ectc" , "notice_period" , "city" , "communication"  , "communication_rating","experience_skillset" , "applicant_summary"]
                            }
                        }
                    }
                }
            ) as response:
                logger.info(f'Recruiting API response status: {response.status}')
                data = await response.json()
                logger.info(f'Full Recruiting API response: {json.dumps(data, indent=2)}')
                return data
    except Exception as e:
        logger.error(f'Error making Recruiting completion call: {e}')
        raise e

# Function to send data to api
async def send_to_webhook(payload: dict):
    logger.info(f'Sending data to webhook: {json.dumps(payload, indent=2)}')
    try:
        with open('candidate_summery.txt', 'w', encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))  # ✅ convert dict to str
        logger.info("candidate_summery.txt created and populated successfully.")
    except Exception as e:
        logger.error(f'Error sending data to webhook: {e}')

# Main function to extract and send customer details
async def process_transcript_and_send(transcript: str, session_id: Optional[str] = None):
    logger.info(f'Starting transcript processing for session {session_id}...')
    try:
        # Make the ChatGPT completion call
        result = await make_chatgpt_completion(transcript)

        logger.info(f'Raw result from ChatGPT: {json.dumps(result, indent=2)}')

        if (result.get('choices') and 
            len(result['choices']) > 0 and 
            result['choices'][0].get('message') and 
            result['choices'][0]['message'].get('content')):
            
            content = result['choices'][0]['message']['content']
            logger.info(f'Raw content from ChatGPT: {content}')
            
            try:
                # Try to parse as JSON first
                parsed_content = json.loads(content)
                logger.info(f'Parsed content: {json.dumps(parsed_content, indent=2)}')

                if parsed_content:
                    # Send the parsed content directly to the webhook
                    await send_to_webhook(parsed_content)
                    logger.info(f'Extracted and sent customer details: {parsed_content}')
                else:
                    logger.error('Unexpected JSON structure in ChatGPT response')
                    
            except json.JSONDecodeError as e:
                logger.error(f'Content is not valid JSON: {e}')
                logger.error(f'Raw content: {repr(content)}')
                
                # Create a fallback structure with the raw content
                fallback_data = {
                    "total_experience": 0,
                    "ctc": 0, 
                    "ectc": 0,
                    "notice_period": "Unknown",
                    "city": "Unknown",
                    "communication": "Unknown",
                    "communication_rating" : 0,
                    "experience_skillset" : [],
                    "applicant_summary" : "Unknown"
                }
                
                logger.info(f'Sending fallback data: {fallback_data}')
                await send_to_webhook(fallback_data)
                
        else:
            logger.error('Unexpected response structure from ChatGPT API')

    except Exception as e:
        logger.error(f'Error in process_transcript_and_send: {e}')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)