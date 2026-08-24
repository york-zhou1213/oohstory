import 'package:flutter/material.dart';

class OohPrimaryDestination {
  final String label;
  final IconData icon;
  final IconData selectedIcon;

  const OohPrimaryDestination({
    required this.label,
    required this.icon,
    required this.selectedIcon,
  });
}

/// A compact four-destination bar whose selected surface contains the icon
/// and label together. Material's stock navigation indicator only surrounds
/// the icon, which makes the label feel visually detached on a reading app.
class OohPrimaryNavigationBar extends StatelessWidget {
  final int selectedIndex;
  final ValueChanged<int> onDestinationSelected;
  final List<OohPrimaryDestination> destinations;

  const OohPrimaryNavigationBar({
    super.key,
    required this.selectedIndex,
    required this.onDestinationSelected,
    required this.destinations,
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;

    return Material(
      color: colors.surface.withValues(alpha: .98),
      child: DecoratedBox(
        decoration: BoxDecoration(
          border: Border(
            top: BorderSide(color: colors.outlineVariant.withValues(alpha: .7)),
          ),
        ),
        child: SafeArea(
          top: false,
          child: SizedBox(
            height: 64,
            child: Row(
              children: List.generate(destinations.length, (index) {
                final destination = destinations[index];
                final selected = index == selectedIndex;
                return Expanded(
                  child: Semantics(
                    button: true,
                    selected: selected,
                    label: destination.label,
                    onTap: () => onDestinationSelected(index),
                    excludeSemantics: true,
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(5, 7, 5, 7),
                      child: InkWell(
                        key: ValueKey('primary-navigation-$index'),
                        onTap: () => onDestinationSelected(index),
                        borderRadius: BorderRadius.circular(16),
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 180),
                          curve: Curves.easeOutCubic,
                          decoration: BoxDecoration(
                            color: selected
                                ? colors.primaryContainer
                                : Colors.transparent,
                            borderRadius: BorderRadius.circular(16),
                          ),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(
                                selected
                                    ? destination.selectedIcon
                                    : destination.icon,
                                size: 21,
                                color: selected
                                    ? colors.onPrimaryContainer
                                    : colors.onSurfaceVariant,
                              ),
                              const SizedBox(height: 2),
                              Text(
                                destination.label,
                                maxLines: 1,
                                style: theme.textTheme.labelSmall?.copyWith(
                                  color: selected
                                      ? colors.onPrimaryContainer
                                      : colors.onSurfaceVariant,
                                  fontWeight: selected
                                      ? FontWeight.w800
                                      : FontWeight.w600,
                                  height: 1.05,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ),
                );
              }),
            ),
          ),
        ),
      ),
    );
  }
}
