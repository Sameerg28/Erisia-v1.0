import logging
import asyncio
import edge_tts
import pygame
import os
import speech_recognition as sr
from faster_whisper import WhisperModel

logger = logging.getLogger("erisia_voice")

class VoiceManager:
    """SINGLE RESPONSIBILITY: Handle Erisia's physical hearing (STT) and speaking (TTS)."""
    
    def __init__(self, tts_voice: str = "en-US-AriaNeural"):
        self.tts_voice = tts_voice
        self.audio_file = "erisia_speech_temp.mp3"
        
        pygame.mixer.init()
        
        logger.info("Booting Auditory Cortex (Whisper Base)...")
        try:
            self.whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            self.recognizer = sr.Recognizer()
        except Exception as e:
            logger.error(f"Failed to boot Whisper: {e}")

    async def _generate_audio(self, text: str):
        """Asynchronously generate the mp3 file using Edge's Neural TTS."""
        communicate = edge_tts.Communicate(text, self.tts_voice, rate="+10%", pitch="+5Hz")
        await communicate.save(self.audio_file)

    def speak(self, text: str):
        """Convert text to speech and play it out loud."""
        if not text:
            return
            
        logger.info(f"Erisia speaking: {text}")
        
        asyncio.run(self._generate_audio(text))
        
        try:
            pygame.mixer.music.load(self.audio_file)
            pygame.mixer.music.play()
            
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
                
            pygame.mixer.music.unload()
            if os.path.exists(self.audio_file):
                os.remove(self.audio_file)
                
        except Exception as e:
            logger.error(f"Playback failed: {e}")

    def listen(self) -> str:
        """Listen to the microphone and transcribe using local Whisper."""
        with sr.Microphone() as source:
            logger.info("Listening...")
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=15)
                
                with open("temp_listen.wav", "wb") as f:
                    f.write(audio.get_wav_data())
                    
                segments, _ = self.whisper_model.transcribe("temp_listen.wav", beam_size=1)
                transcription = " ".join([segment.text for segment in segments]).strip()
                
                if os.path.exists("temp_listen.wav"):
                    os.remove("temp_listen.wav")
                    
                logger.info(f"Heard: {transcription}")
                return transcription
                
            except sr.WaitTimeoutError:
                return ""
            except Exception as e:
                logger.error(f"Hearing error: {e}")
                return ""

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Initializing Voice System...")
    voice = VoiceManager()
    
    print("Testing TTS...")
    voice.speak("Hello Master Sameer. My vocal systems are now online.")
    
    print("Testing STT... Please say something into the microphone.")
    heard_text = voice.listen()
    if heard_text:
        voice.speak(f"I heard you say: {heard_text}")
    else:
        print("Did not hear anything.")
