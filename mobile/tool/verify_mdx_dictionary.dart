import 'dart:io';

import 'package:oohstory/adapters/dictionary/dictionary.dart';

Future<void> main(List<String> arguments) async {
  if (arguments.isEmpty || arguments.length > 2) {
    stderr.writeln(
      'Usage: dart run tool/verify_mdx_dictionary.dart file.mdx [file.mdd]\n'
      '   or: dart run tool/verify_mdx_dictionary.dart file.mdd',
    );
    exitCode = 64;
    return;
  }
  if (arguments.length == 1 && arguments.first.toLowerCase().endsWith('.mdd')) {
    final mdd = MddResourceAdapter.fromBytes(
      await File(arguments.first).readAsBytes(),
    );
    stdout.writeln('MDD resources: ${mdd.resourceCount}');
    return;
  }
  final mdxFile = File(arguments.first);
  final mdx = MdxDictionaryAdapter.fromBytes(await mdxFile.readAsBytes());
  stdout.writeln('MDX entries: ${mdx.entryCount}');
  if (arguments.length == 2) {
    final mddFile = File(arguments[1]);
    final mdd = MddResourceAdapter.fromBytes(await mddFile.readAsBytes());
    stdout.writeln('MDD resources: ${mdd.resourceCount}');
  }
}
