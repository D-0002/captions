#!/usr/bin/env python3
"""
AssemblyAI API Key Tester
Test if your API key is working correctly
"""

import requests
import struct

def create_silent_wav_data():
    """Creates the byte data for a short, silent WAV file."""
    # WAV Header Parameters
    sample_rate = 8000
    num_channels = 1
    bits_per_sample = 16
    duration_in_seconds = 1
    num_samples = sample_rate * duration_in_seconds

    # Start with the RIFF header
    wav_data = b'RIFF'
    
    # ChunkSize: 36 + SubChunk2Size
    subchunk2_size = num_samples * num_channels * bits_per_sample // 8
    chunk_size = 36 + subchunk2_size
    wav_data += struct.pack('<I', chunk_size)
    
    wav_data += b'WAVE'
    
    # 'fmt ' sub-chunk
    wav_data += b'fmt '
    wav_data += struct.pack('<I', 16)  # Subchunk1Size for PCM
    wav_data += struct.pack('<H', 1)   # AudioFormat for PCM
    wav_data += struct.pack('<H', num_channels)
    wav_data += struct.pack('<I', sample_rate)
    
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    wav_data += struct.pack('<I', byte_rate)
    
    block_align = num_channels * bits_per_sample // 8
    wav_data += struct.pack('<H', block_align)
    
    wav_data += struct.pack('<H', bits_per_sample)
    
    # 'data' sub-chunk
    wav_data += b'data'
    wav_data += struct.pack('<I', subchunk2_size)
    
    # Add silent audio data (bytes of zeros)
    wav_data += b'\x00' * subchunk2_size
    
    return wav_data

def test_api_key(api_key):
    """Test if the API key is valid"""
    headers = {"authorization": api_key}
    base_url = "https://api.assemblyai.com"
    
    print("Testing AssemblyAI API Key...")
    print(f"API Key: {api_key[:10]}..." + "*" * (len(api_key) - 10))
    
    try:
        # Test 1: Try to access the API with a simple request
        response = requests.get(f"{base_url}/v2/transcript", headers=headers)
        print(f"API Response Status: {response.status_code}")
        
        if response.status_code == 401:
            print("❌ FAILED: Invalid API key or unauthorized access")
            return False
        elif response.status_code == 200:
            print("✅ SUCCESS: API key is valid")
            
            # Test 2: Try uploading a small, valid, silent audio file
            # The previous error (422) was because "test audio data" is not a valid audio file.
            test_data = create_silent_wav_data()
            upload_response = requests.post(
                f"{base_url}/v2/upload",
                headers=headers,
                data=test_data
            )
            
            print(f"Upload Test Status: {upload_response.status_code}")
            if upload_response.status_code == 200:
                print("✅ SUCCESS: Upload endpoint works")
                return True
            else:
                print(f"⚠️  WARNING: Upload test failed: {upload_response.text}")
                return False
        else:
            print(f"⚠️  WARNING: Unexpected response: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ FAILED: Network error: {e}")
        return False
    except Exception as e:
        print(f"❌ FAILED: Unexpected error: {e}")
        return False

if __name__ == "__main__":
    # Test the API key
    api_key = "55b99db8c9804e31bb1d978f81766379"
    result = test_api_key(api_key)
    
    if result:
        print("\n🎉 Your API key is working correctly!")
    else:
        print("\n💡 Possible solutions:")
        print("1. Check if your API key is correct")
        print("2. Verify your AssemblyAI account is active")
        print("3. Check your internet connection")
        print("4. Try generating a new API key from AssemblyAI dashboard")