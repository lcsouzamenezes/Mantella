import sys
from faster_whisper import WhisperModel
import speech_recognition as sr
import logging
from src.config.config_loader import ConfigLoader
import src.utils as utils
import requests
import json
import io
from pathlib import Path

class Transcriber:
    def __init__(self, config: ConfigLoader, stt_secret_key_file: str, secret_key_file: str):
        self.loglevel = 27
        # self.mic_enabled = config.mic_enabled
        self.language = config.stt_language
        self.task = "transcribe"
        if config.stt_translate == 1:
            # translate to English
            self.task = "translate"
        self.model = config.whisper_model
        self.process_device = config.whisper_process_device
        self.audio_threshold = config.audio_threshold
        self.listen_timeout = config.listen_timeout
        self.whisper_type = config.whisper_type
        self.whisper_url = config.whisper_url

        #self.debug_mode = config.debug_mode
        #self.debug_use_default_player_response = config.debug_use_default_player_response
        #self.default_player_response = config.default_player_response
        #self.debug_exit_on_first_exchange = config.debug_exit_on_first_exchange
        self.end_conversation_keyword = config.end_conversation_keyword
        self.radiant_start_prompt = config.radiant_start_prompt
        self.radiant_end_prompt = config.radiant_end_prompt

        self.call_count = 0
        self.__stt_secret_key_file = stt_secret_key_file
        self.__secret_key_file = secret_key_file
        self.__api_key: str | None = None
        self.__ignore_list = ['', 'thank you', 'thank you for watching', 'thanks for watching', 'the transcript is from the', 'the', 'thank you very much', "thank you for watching and i'll see you in the next video", "we'll see you in the next video", 'see you next time']
            

        # if self.mic_enabled == '1':
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = config.pause_threshold
        self.microphone = sr.Microphone()

        if self.audio_threshold == 'auto':
            logging.log(self.loglevel, f"Audio threshold set to 'auto'. Adjusting microphone for ambient noise...")
            logging.log(self.loglevel, "If the mic is not picking up your voice, try setting this audio_threshold value manually in MantellaSoftware/config.ini.\n")
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=5)
        else:
            self.recognizer.dynamic_energy_threshold = False
            self.recognizer.energy_threshold = int(self.audio_threshold)
            logging.log(self.loglevel, f"Audio threshold set to {self.audio_threshold}. If the mic is not picking up your voice, try lowering this value in MantellaSoftware/config.ini. If the mic is picking up too much background noise, try increasing this value.\n")

        # if using faster_whisper, load model selected by player, otherwise skip this step
        if self.whisper_type == 'faster_whisper':
            if self.process_device == 'cuda':
                self.transcribe_model = WhisperModel(self.model, device=self.process_device)
            else:
                self.transcribe_model = WhisperModel(self.model, device=self.process_device, compute_type="float32")

    @utils.time_it
    def __get_api_key(self) -> str:
        if not self.__api_key:
            try: # first check mod folder for stt secret key
                mod_parent_folder = str(Path(utils.resolve_path()).parent.parent.parent)
                with open(mod_parent_folder+'\\'+self.__stt_secret_key_file, 'r') as f:
                    self.__api_key: str = f.readline().strip()
            except: # check locally (same folder as exe) for stt secret key
                try:
                    with open(self.__stt_secret_key_file, 'r') as f:
                        self.__api_key: str = f.readline().strip()
                except:
                    try: # first check mod folder for secret key
                        mod_parent_folder = str(Path(utils.resolve_path()).parent.parent.parent)
                        with open(mod_parent_folder+'\\'+self.__secret_key_file, 'r') as f:
                            self.__api_key: str = f.readline().strip()
                    except: # check locally (same folder as exe) for secret key
                        with open(self.__secret_key_file, 'r') as f:
                            self.__api_key: str = f.readline().strip()
                
            if not self.__api_key:
                logging.error(f'''No secret key found in GPT_SECRET_KEY.txt. Please create a secret key and paste it in your Mantella mod folder's GPT_SECRET_KEY.txt file.
If you are using OpenRouter (default), you can create a secret key in Account -> Keys once you have created an account: https://openrouter.ai/
If using OpenAI, see here on how to create a secret key: https://help.openai.com/en/articles/4936850-where-do-i-find-my-openai-api-key
If you are running a model locally, please ensure the service (Kobold / Text generation web UI) is running.''')
                input("Press Enter to continue.")
                sys.exit(0)
        return self.__api_key          


    @utils.time_it
    def recognize_input(self, prompt: str):
        """
        Recognize input from mic and return transcript if activation tag (assistant name) exist
        """
        while True:
            # self.game_state_manager.write_game_info('_mantella_status', 'Listening...')
            logging.log(self.loglevel, 'Listening...')
            transcript = self._recognize_speech_from_mic(prompt)
            if transcript == None:
                continue

            transcript_cleaned = utils.clean_text(transcript)

            # conversation_ended = self.game_state_manager.load_data_when_available('_mantella_end_conversation', '')
            # if conversation_ended.lower() == 'true':
            #     return 'goodbye'

            # common phrases hallucinated by Whisper
            if transcript_cleaned in self.__ignore_list:
                continue

            # self.game_state_manager.write_game_info('_mantella_status', 'Thinking...')
            return transcript
    

    @utils.time_it
    def _recognize_speech_from_mic(self, prompt:str):
        """
        Capture the words from the recorded audio (audio stream --> free text).
        Transcribe speech from recorded from `microphone`.
        """
        @utils.time_it
        def whisper_transcribe(audio, prompt: str):
            # if using faster_whisper (default) return based on faster_whisper's code, if not assume player wants to use server mode and send query to whisper_url set by player.
            if self.whisper_type == 'faster_whisper':
                segments, info = self.transcribe_model.transcribe(audio, task=self.task, language=self.language, beam_size=5, vad_filter=True, initial_prompt=prompt)
                result_text = ' '.join(segment.text for segment in segments)

                return result_text
            # this code queries the whispercpp server set by the user to obtain the response, this format also allows use of official openai whisper API
            else:
                url = self.whisper_url
                if 'openai' in url:
                    headers = {"Authorization": f"Bearer {self.__get_api_key()}",}
                else:
                    headers = {"Authorization": "Bearer apikey",}
                data = {'model': self.model, 'prompt': prompt}
                files = {'file': ('audio.wav', audio, 'audio/wav')}
                response = requests.post(url, headers=headers, files=files, data=data)
                if response.status_code != 200:
                    logging.error(f'STT Error: {response.content}')
                response_data = json.loads(response.text)
                if 'text' in response_data:
                    return response_data['text'].strip()

        with self.microphone as source:
            try:
                audio = self.recognizer.listen(source, timeout=self.listen_timeout)
            except sr.WaitTimeoutError:
                return ''
        
        audio_data = audio.get_wav_data(convert_rate=16000)
        audio_file = io.BytesIO(audio_data)
        transcript = whisper_transcribe(audio_file, prompt)
        if transcript:
            logging.log(self.loglevel, transcript)

        return transcript


    @staticmethod
    @utils.time_it
    def activation_name_exists(transcript_cleaned, activation_name):
        """Identifies keyword in the input transcript"""

        keyword_found = False
        if transcript_cleaned:
            transcript_words = transcript_cleaned.split()
            if bool(set(transcript_words).intersection([activation_name])):
                keyword_found = True
            elif transcript_cleaned == activation_name:
                keyword_found = True
        
        return keyword_found


    @staticmethod
    @utils.time_it
    def _remove_activation_word(transcript, activation_name):
        transcript = transcript.replace(activation_name, '')
        return transcript