import 'package:audio_service/audio_service.dart';
import 'package:just_audio/just_audio.dart';

class TtsAudioHandler extends BaseAudioHandler with SeekHandler {
  final AudioPlayer player = AudioPlayer();

  String _title = '听书';
  String _album = '';
  String _artist = 'OOH Story';
  Uri? _artUri;

  void Function()? onSkipPrev;
  void Function()? onSkipNext;

  TtsAudioHandler() {
    player.playerStateStream.listen((state) {
      playbackState.add(_buildState(state));
    });
  }

  void updateMetadata({
    String? title,
    String? album,
    String? artist,
    Uri? artUri,
  }) {
    if (title != null) _title = title;
    if (album != null) _album = album;
    if (artist != null) _artist = artist;
    if (artUri != null) _artUri = artUri;
    mediaItem.add(
      MediaItem(
        id: 'tts',
        title: _title,
        album: _album,
        artist: _artist,
        artUri: _artUri,
      ),
    );
  }

  PlaybackState _buildState(PlayerState state) {
    final playing = state.playing;
    final processingState = switch (state.processingState) {
      ProcessingState.idle => AudioProcessingState.idle,
      ProcessingState.loading => AudioProcessingState.loading,
      ProcessingState.buffering => AudioProcessingState.buffering,
      ProcessingState.ready => AudioProcessingState.ready,
      ProcessingState.completed => AudioProcessingState.completed,
    };

    return PlaybackState(
      controls: [
        MediaControl.skipToPrevious,
        playing ? MediaControl.pause : MediaControl.play,
        MediaControl.skipToNext,
        MediaControl.stop,
      ],
      systemActions: const {
        MediaAction.play,
        MediaAction.pause,
        MediaAction.stop,
        MediaAction.skipToPrevious,
        MediaAction.skipToNext,
      },
      playing: playing,
      processingState: processingState,
      updatePosition: player.position,
    );
  }

  @override
  Future<void> play() async => player.play();

  @override
  Future<void> pause() async => player.pause();

  @override
  Future<void> stop() async {
    await player.stop();
    playbackState.add(
      PlaybackState(processingState: AudioProcessingState.idle, playing: false),
    );
  }

  @override
  Future<void> seek(Duration position) async => player.seek(position);

  @override
  Future<void> skipToPrevious() async => onSkipPrev?.call();

  @override
  Future<void> skipToNext() async => onSkipNext?.call();
}
