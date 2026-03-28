### YouTube Video Subtitle Transcription using Google Cloud Speech-to-Text API
#### Overview

The following Python code utilizes the `google-cloud-speech` and `google-api-python-client` libraries to automatically transcribe video subtitles from YouTube videos.

#### Prerequisites

*   Google Cloud Account
*   Google Cloud Speech-to-Text API enabled
*   Google API key or OAuth credentials
*   `google-cloud-speech` and `google-api-python-client` libraries installed

#### Code

```python
# Import required libraries
from google.cloud import speech
from googleapiclient.discovery import build
import json

# Define TOOL_SCHEMA dictionary
TOOL_SCHEMA = {
    "name": "YouTube Subtitle Transcription",
    "description": "Transcribe video subtitles from YouTube videos using Google Cloud Speech-to-Text API",
    "version": "1.0",
    "author": "Erisia's Subconscious Daemon"
}

def execute_skill(**kwargs):
    """
    Execute the subtitle transcription skill.
    
    Args:
        kwargs (dict): Keyword arguments containing the YouTube video URL and transcription settings.
        
    Returns:
        str: Transcribed subtitles.
    """
    
    # Extract video URL and transcription settings from kwargs
    video_url = kwargs.get("video_url")
    language_code = kwargs.get("language_code", "en-US")
    sample_rate_hertz = kwargs.get("sample_rate_hertz", 16000)
    
    # Create a YouTube API client
    youtube = build('youtube', 'v3', developerKey="YOUR_API_KEY")
    
    # Extract audio from the YouTube video
    # NOTE: This step is not implemented here, as it requires additional libraries and complexity.
    # For simplicity, assume we have the audio content as a byte string.
    audio_content = b"YOUR_AUDIO_CONTENT"
    
    # Create a Speech-to-Text client
    client = speech.SpeechClient()
    
    # Configure the transcription settings
    config = speech.types.RecognitionConfig(
        encoding=speech.types.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate_hertz,
        language_code=language_code
    )
    
    # Perform the transcription
    audio = speech.types.RecognitionAudio(content=audio_content)
    response = client.recognize(config, audio)
    
    # Extract the transcribed subtitles
    subtitles = []
    for result in response.results:
        for alternative in result.alternatives:
            subtitles.append(alternative.transcript)
    
    # Return the transcribed subtitles
    return "\n".join(subtitles)

# Example usage
video_url = "https://www.youtube.com/watch?v=izdDHVLc_Z0"
transcribed_subtitles = execute_skill(video_url=video_url)
print(transcribed_subtitles)
```

#### Notes

*   This code assumes you have the `google-cloud-speech` and `google-api-python-client` libraries installed.
*   You need to replace `YOUR_API_KEY` with your actual YouTube API key.
*   The `execute_skill` function takes keyword arguments `video_url`, `language_code`, and `sample_rate_hertz`.
*   The transcription settings (e.g., language code, sample rate) can be customized by passing the corresponding keyword arguments.
*   The audio extraction step is not implemented here, as it requires additional libraries and complexity. You can use libraries like `pydub` or `moviepy` to extract the audio from the YouTube video.

#### Commit Message

`feat: add YouTube subtitle transcription skill using Google Cloud Speech-to-Text API`

#### API Documentation

You can find the API documentation for the `google-cloud-speech` and `google-api-python-client` libraries at the following links:

*   <https://googleapis.dev/python/speech/latest/index.html>
*   <https://developers.google.com/api-client-library/python/apis/youtube/v3>