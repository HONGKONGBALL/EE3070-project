from summarize_v2_timing import summarize


def test_timing_joins_event_identity_not_repeated_phrase_or_log_arrival_order():
    rows=[
        {'record_type':'warning','event_id':'first','phrase_id':'puddle','confirmed_at':11,'confirmation_latency_ms':800},
        {'record_type':'warning','event_id':'second','phrase_id':'puddle','confirmed_at':21,'confirmation_latency_ms':900},
        {'record_type':'audio_start','playback_id':'a','process_started_at':11.1},
        {'record_type':'audio_start','playback_id':'b','process_started_at':21.2},
        {'record_type':'arbiter','action':'played','event_id':'second','playback_id':'b'},
        {'record_type':'arbiter','action':'played','event_id':'first','playback_id':'a'},
        {'record_type':'audio_interrupted','playback_id':'a'},
        {'record_type':'audio_done','playback_id':'b'},
        {'record_type':'arbiter','action':'preempted','event_id':'first','playback_id':'a','monotonic':12},
        {'record_type':'audio_stopped','playback_id':'a','stopped_at':12.015},
    ]
    result=summarize(rows)
    assert result['events'][0]['playbacks']==[{'playback_id':'a','outcome':'audio_interrupted','confirmation_to_process_ms':100}]
    assert result['events'][1]['playbacks'][0]['outcome']=='audio_done'
    assert result['timings']['confirmation_to_player_process']['p95_ms']==195
    assert result['timings']['preempting_submit_to_old_process_stopped']['max_ms']==15


def test_legacy_or_rejected_dispatch_does_not_become_audio_success():
    result=summarize([
        {'record_type':'warning','event_id':'event','phrase_id':'chair'},
        {'record_type':'arbiter','action':'played','event_id':'event','playback_id':None},
        {'record_type':'audio_start','phrase_id':'chair'},
        {'record_type':'audio_error','phrase_id':'chair'},
    ])
    assert result['events'][0]['playbacks']==[]
    assert result['legacy_audio_starts_without_identity']==1
    assert result['timings']['first_visible_capture_to_confirmation']=={'samples':0}
