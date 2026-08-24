import 'dart:async';

import 'package:flutter/material.dart';

OverlayEntry? _activeAccountSuccessToast;

void showAccountSuccessToast(BuildContext context, {required String message}) {
  _activeAccountSuccessToast?.remove();
  _activeAccountSuccessToast = null;
  final overlay = Overlay.of(context, rootOverlay: true);
  late final OverlayEntry entry;
  entry = OverlayEntry(
    builder: (_) => _AccountSuccessToast(
      message: message,
      onDismissed: () {
        if (!identical(_activeAccountSuccessToast, entry)) return;
        entry.remove();
        _activeAccountSuccessToast = null;
      },
    ),
  );
  _activeAccountSuccessToast = entry;
  overlay.insert(entry);
}

class _AccountSuccessToast extends StatefulWidget {
  const _AccountSuccessToast({
    required this.message,
    required this.onDismissed,
  });

  final String message;
  final VoidCallback onDismissed;

  @override
  State<_AccountSuccessToast> createState() => _AccountSuccessToastState();
}

class _AccountSuccessToastState extends State<_AccountSuccessToast>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;
  late final Animation<double> _opacity;
  late final Animation<Offset> _slide;
  Timer? _timer;
  bool _closing = false;

  @override
  void initState() {
    super.initState();
    _controller =
        AnimationController(
          vsync: this,
          duration: const Duration(milliseconds: 260),
          reverseDuration: const Duration(milliseconds: 720),
        )..addStatusListener((status) {
          if (_closing && status == AnimationStatus.dismissed) {
            widget.onDismissed();
          }
        });
    final curve = CurvedAnimation(
      parent: _controller,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );
    _opacity = curve;
    _slide = Tween<Offset>(
      begin: const Offset(0, -.18),
      end: Offset.zero,
    ).animate(curve);
    _controller.forward();
    _timer = Timer(const Duration(milliseconds: 1750), () {
      if (!mounted) return;
      _closing = true;
      _controller.reverse();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Positioned(
      top: MediaQuery.paddingOf(context).top + 18,
      left: 20,
      right: 20,
      child: IgnorePointer(
        child: Align(
          alignment: Alignment.topCenter,
          child: FadeTransition(
            opacity: _opacity,
            child: SlideTransition(
              position: _slide,
              child: Semantics(
                liveRegion: true,
                container: true,
                label: widget.message,
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 420),
                  child: Material(
                    elevation: 16,
                    shadowColor: const Color(0x33204E39),
                    color: const Color(0xFFF6FFF9),
                    shape: RoundedRectangleBorder(
                      side: const BorderSide(color: Color(0x4D50B581)),
                      borderRadius: BorderRadius.circular(17),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 11, 16, 11),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: 32,
                            height: 32,
                            alignment: Alignment.center,
                            decoration: BoxDecoration(
                              gradient: const LinearGradient(
                                colors: [Color(0xFF64D69B), Color(0xFF2C9F70)],
                              ),
                              borderRadius: BorderRadius.circular(11),
                            ),
                            child: const Icon(
                              Icons.check_rounded,
                              size: 20,
                              color: Colors.white,
                            ),
                          ),
                          const SizedBox(width: 11),
                          Flexible(
                            child: Text(
                              widget.message,
                              style: const TextStyle(
                                color: Color(0xFF183D2D),
                                fontSize: 14,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
