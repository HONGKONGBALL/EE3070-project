# Run on Windows once; the Pi only plays the generated WAV files offline.
param([string]$OutputDirectory = './audio/en')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$cameraAudioDir = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $cameraAudioDir | Out-Null
$cameraSynth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$cameraSynth.SelectVoice('Microsoft David Desktop')
$cameraSynth.Rate = 1
$cameraPhrases = @{
    person_ahead = 'Person ahead. Please be careful.'
    chair_ahead = 'Chair ahead. Please be careful.'
    backpack_ahead = 'Bag ahead. Please be careful.'
    camera_unavailable = 'Camera unavailable.'
    visual_unavailable = 'Visual detection unavailable.'
    visual_ready = 'Visual assistance ready.'
    bicycle_ahead = 'Bicycle ahead.'
    obstacle_ahead = 'Obstacle ahead.'
    pothole_ahead = 'Possible pothole ahead.'
    damaged_pavement = 'Possible damaged pavement ahead.'
    puddle_ahead = 'Possible wet pavement ahead.'
    stairs_ahead = 'Possible stairs ahead.'
    downstairs_ahead = 'Possible steps down ahead. Watch your step.'
    object_unavailable = 'Object detection unavailable.'
    ground_unavailable = 'Ground detection unavailable.'
}
try {
    foreach ($cameraPhrase in $cameraPhrases.GetEnumerator()) {
        $cameraWavPath = Join-Path $cameraAudioDir ($cameraPhrase.Key + '.wav')
        $cameraSynth.SetOutputToWaveFile($cameraWavPath)
        $cameraSynth.Speak($cameraPhrase.Value)
        $cameraSynth.SetOutputToNull()
    }
} finally {
    $cameraSynth.Dispose()
}
Write-Output 'Generated English camera prompts.'
