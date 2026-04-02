import logging
import asyncio
import os
from typing import Any

logger = logging.getLogger("erisia_voice")

try:
    import edge_tts
    import pygame
    import speech_recognition as sr
    from faster_whisper import WhisperModel
    VOICE_AVAILABLE = True
except ImportError as e:
    VOICE_AVAILABLE = False
    edge_tts = None
    pygame = None
    sr = None
    WhisperModel = None
    logger.warning(f"Voice dependencies not available: {e}")


TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "speak_text",
        "description": "Convert text to speech and play it out loud. Use this when Erisia needs to verbally communicate with Master Sameer.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to convert to speech and speak aloud."
                }
            },
            "required": ["text"]
        }
    }
}


class VoiceManager:
    """SINGLE RESPONSIBILITY: Handle Erisia's physical speaking (TTS)."""
    
    def __init__(self, tts_voice: str = "en-US-AriaNeural"):
        if not VOICE_AVAILABLE:
            raise RuntimeError("Voice dependencies not installed. Run: pip install edge-tts pygame faster-whisper SpeechRecognition")
        
        self.tts_voice = tts_voice
        self.audio_file = "erisia_speech_temp.mp3"
        
        if VOICE_AVAILABLE and pygame is not None:
            pygame.mixer.init()
        else:
            raise RuntimeError("Voice dependencies not available.")
        
        logger.info("Booting Auditory Cortex (Whisper Base)...")
        try:
            if WhisperModel is not None:
                self.whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            else:
                self.whisper_model = None
            if sr is not None:
                self.recognizer = sr.Recognizer()
            else:
                self.recognizer = None
        except Exception as e:
            logger.error(f"Failed to boot Whisper: {e}")
            self.whisper_model = None
            self.recognizer = None

    async def _generate_audio(self, text: str):
        """Asynchronously generate the mp3 file using Edge's Neural TTS."""
        if edge_tts is None:
            raise RuntimeError("edge_tts not available")
        communicate = edge_tts.Communicate(text, self.tts_voice, rate="+10%", pitch="+5Hz")
        await communicate.save(self.audio_file)

    def speak(self, text: str) -> str:
        """Convert text to speech and play it out loud."""
        if not text:
            return "[VOICE ERROR]: No text provided to speak."
            
        if not VOICE_AVAILABLE:
            return f"[VOICE]: (would speak: {text[:50]}...)"
        
        pg = pygame
        if pg is None:
            return "[VOICE ERROR]: pygame not available"
        
        logger.info(f"Erisia speaking: {text}")
        
        try:
            asyncio.run(self._generate_audio(text))
            
            pg.mixer.music.load(self.audio_file)
            pg.mixer.music.play()
            
            while pg.mixer.music.get_busy():
                pg.time.Clock().tick(10)
            
            pg.mixer.music.unload()
            if os.path.exists(self.audio_file):
                os.remove(self.audio_file)
                
            return f"[VOICE]: Spoke '{text[:100]}...'"
        except Exception as e:
            logger.error(f"Failed to speak: {e}")
            return f"[VOICE ERROR]: {e}"

    def listen(self, timeout: int = 5) -> str:
        """Listen for speech from microphone and transcribe it."""
        if not VOICE_AVAILABLE or not self.whisper_model or not self.recognizer or sr is None:
            return "[VOICE ERROR]: Speech recognition not available."
        
        try:
            with sr.Microphone() as source:
                logger.info("Listening...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = self.recognizer.listen(source, timeout=timeout)
            
            audio_data = audio.get_wav_data()
            temp_wav = "erisia_listen_temp.wav"
            with open(temp_wav, "wb") as f:
                f.write(audio_data)
            
            segments, _ = self.whisper_model.transcribe(temp_wav, beam_size=5)
            text = "".join([seg.text for seg in segments]).strip()
            
            if os.path.exists(temp_wav):
                os.remove(temp_wav)
                
            if text:
                logger.info(f"Transcribed: {text}")
                return text
            return "[VOICE]: No speech detected."
            
        except Exception as e:
            logger.error(f"Failed to listen: {e}")
            return f"[VOICE ERROR]: {e}"


_voice_manager: VoiceManager | None = None

def get_voice_manager() -> VoiceManager:
    """Get or create the singleton VoiceManager instance."""
    global _voice_manager
    if _voice_manager is None:
        try:
            _voice_manager = VoiceManager()
        except RuntimeError as e:
            logger.error(f"Cannot initialize voice: {e}")
            raise
    return _voice_manager

def speak_text(text: str) -> str:
    """Tool function: Convert text to speech and speak aloud."""
    try:
        manager = get_voice_manager()
        return manager.speak(text)
    except RuntimeError as e:
        return f"[VOICE]: {e}"

def listen_for_speech(timeout: int = 5) -> str:
    """Tool function: Listen for speech and return transcribed text."""
    try:
        manager = get_voice_manager()
        return manager.listen(timeout=timeout)
    except RuntimeError as e:
        return f"[VOICE]: {e}"


if __name__ == "__main__":
    print("Testing Erisia Voice...")
    print(speak_text("Hello Master Sameer, my voice is now active!"))
