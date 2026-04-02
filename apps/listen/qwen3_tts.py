"""Qwen3-TTS voice cloning and synthesis via Hugging Face Spaces API.

Uses the free Qwen/Qwen3-TTS Space on HuggingFace for:
- Voice cloning from a reference audio sample
- Voice design from text descriptions
- Custom voice with speaker + instruction control

No API key required. Uses gradio_client.

Usage:
    python qwen3_tts.py clone <ref_audio> <ref_text> <target_text> [--output out.wav]
    python qwen3_tts.py design <text> <voice_description> [--output out.wav]
    python qwen3_tts.py custom <text> <speaker> <instruct> [--output out.wav]
"""

import argparse
import shutil
import sys
from pathlib import Path

from gradio_client import Client

SPACE_ID = "Qwen/Qwen3-TTS"


def clone_voice(ref_audio: str, ref_text: str, target_text: str, output: str = "clone_output.wav"):
    """Clone a voice from a reference audio and generate new speech."""
    c = Client(SPACE_ID)
    result = c.predict(
        ref_audio=ref_audio,
        ref_text=ref_text,
        target_text=target_text,
        language="English",
        use_xvector_only=False,
        model_size="1.7B",
        api_name="/generate_voice_clone",
    )
    audio_path, status = result
    shutil.copy2(audio_path, output)
    print(f"Cloned voice saved to: {output}")
    print(f"Status: {status}")
    return output


def design_voice(text: str, voice_description: str, output: str = "design_output.wav"):
    """Generate speech with a voice designed from a text description."""
    c = Client(SPACE_ID)
    result = c.predict(
        text=text,
        language="English",
        voice_description=voice_description,
        api_name="/generate_voice_design",
    )
    audio_path, status = result
    shutil.copy2(audio_path, output)
    print(f"Designed voice saved to: {output}")
    print(f"Status: {status}")
    return output


def custom_voice(text: str, speaker: str, instruct: str, output: str = "custom_output.wav"):
    """Generate speech with a built-in speaker and custom instruction."""
    c = Client(SPACE_ID)
    result = c.predict(
        text=text,
        language="English",
        speaker=speaker,
        instruct=instruct,
        model_size="1.7B",
        api_name="/generate_custom_voice",
    )
    audio_path, status = result
    shutil.copy2(audio_path, output)
    print(f"Custom voice saved to: {output}")
    print(f"Status: {status}")
    return output


def main():
    parser = argparse.ArgumentParser(description="Qwen3-TTS voice synthesis")
    sub = parser.add_subparsers(dest="command")

    # Clone
    p_clone = sub.add_parser("clone", help="Clone a voice from reference audio")
    p_clone.add_argument("ref_audio", help="Path to reference audio file")
    p_clone.add_argument("ref_text", help="Transcript of the reference audio")
    p_clone.add_argument("target_text", help="Text to synthesize with cloned voice")
    p_clone.add_argument("--output", default="clone_output.wav")

    # Design
    p_design = sub.add_parser("design", help="Design a voice from description")
    p_design.add_argument("text", help="Text to synthesize")
    p_design.add_argument("voice_description", help="Description of desired voice")
    p_design.add_argument("--output", default="design_output.wav")

    # Custom
    p_custom = sub.add_parser("custom", help="Use built-in speaker with instruction")
    p_custom.add_argument("text", help="Text to synthesize")
    p_custom.add_argument("speaker", choices=["Aiden", "Dylan", "Eric", "Ono_anna", "Ryan", "Serena", "Sohee", "Uncle_fu", "Vivian"])
    p_custom.add_argument("instruct", help="Voice instruction/style description")
    p_custom.add_argument("--output", default="custom_output.wav")

    args = parser.parse_args()

    if args.command == "clone":
        clone_voice(args.ref_audio, args.ref_text, args.target_text, args.output)
    elif args.command == "design":
        design_voice(args.text, args.voice_description, args.output)
    elif args.command == "custom":
        custom_voice(args.text, args.speaker, args.instruct, args.output)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
