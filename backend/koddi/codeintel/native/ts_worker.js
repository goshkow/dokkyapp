// Языковой сервис TypeScript проекта для Докки: определения, ссылки, переименование, символы, диагностика,
// извлечение функции. Запускается с TypeScript из node_modules самого проекта, общается строками JSON.
// Аргументы: <корень проекта> <путь к typescript.js>. Позиции - строка с 1 и символ в UTF-16 с 0.
'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const root = path.resolve(process.argv[2]);
const ts = require(process.argv[3]);
const MAX_RESULTS = 2000;

const projects = new Map(); // путь tsconfig (или '' для файлов без него) -> проект
const snapshots = new Map(); // файл -> {version, snapshot}

function inside(file) {
  const relative = path.relative(root, file);
  return relative && !relative.startsWith('..') && !path.isAbsolute(relative);
}

function version(file) {
  try {
    const stat = fs.statSync(file);
    return `${stat.mtimeMs}:${stat.size}`;
  } catch (error) {
    return 'missing';
  }
}

function snapshot(file) {
  const current = version(file);
  const cached = snapshots.get(file);
  if (cached && cached.version === current) return cached.snapshot;
  if (current === 'missing') return undefined;
  const text = fs.readFileSync(file, 'utf8');
  const value = ts.ScriptSnapshot.fromString(text);
  snapshots.set(file, { version: current, snapshot: value });
  return value;
}

function parseConfig(configPath) {
  const parsed = ts.getParsedCommandLineOfConfigFile(configPath, {}, {
    ...ts.sys,
    onUnRecoverableConfigFileDiagnostic: () => {},
  });
  if (!parsed) return null;
  let fileNames = parsed.fileNames.slice();
  // решение (files: [] + references): файлы берутся из связанных конфигураций
  for (const reference of parsed.projectReferences || []) {
    const target = ts.resolveProjectReferencePath(reference);
    const inner = fs.existsSync(target) ? parseConfig(target) : null;
    if (inner) {
      fileNames = fileNames.concat(inner.fileNames);
      if (!parsed.fileNames.length && !parsed.raw.compilerOptions) parsed.options = { ...inner.options, ...parsed.options };
    }
  }
  return { options: parsed.options, fileNames: Array.from(new Set(fileNames)) };
}

function createProject(key, options, fileNames, directory) {
  const files = new Set(fileNames);
  const host = {
    getScriptFileNames: () => Array.from(files),
    getScriptVersion: version,
    getScriptSnapshot: snapshot,
    getCurrentDirectory: () => directory,
    getCompilationSettings: () => options,
    getDefaultLibFileName: (value) => ts.getDefaultLibFilePath(value),
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
    realpath: ts.sys.realpath,
    useCaseSensitiveFileNames: () => ts.sys.useCaseSensitiveFileNames,
  };
  const project = { key, files, service: ts.createLanguageService(host, ts.createDocumentRegistry()) };
  projects.set(key, project);
  return project;
}

function configFor(file) {
  let directory = path.dirname(file);
  while (inside(directory) || directory === root) {
    const candidate = path.join(directory, 'tsconfig.json');
    if (fs.existsSync(candidate)) return candidate;
    const js = path.join(directory, 'jsconfig.json');
    if (fs.existsSync(js)) return js;
    if (directory === root) break;
    directory = path.dirname(directory);
  }
  return '';
}

function projectFor(file) {
  const config = configFor(file);
  if (config) {
    let project = projects.get(config);
    if (!project) {
      const parsed = parseConfig(config);
      if (parsed) project = createProject(config, parsed.options, parsed.fileNames, path.dirname(config));
    }
    if (project) {
      if (!project.files.has(file)) {
        // файл вне include конфигурации (новый или тестовый) всё равно разбирается вместе с проектом
        project.files.add(file);
      }
      return project;
    }
  }
  let inferred = projects.get('');
  if (!inferred) {
    inferred = createProject('', {
      allowJs: true, checkJs: false, jsx: ts.JsxEmit.Preserve, target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler || ts.ModuleResolutionKind.NodeJs,
      allowNonTsExtensions: true, noEmit: true, skipLibCheck: true,
    }, [], root);
  }
  inferred.files.add(file);
  return inferred;
}

function sourceFile(project, file) {
  const program = project.service.getProgram();
  const source = program && program.getSourceFile(file);
  if (!source) throw new Error(`file is not part of the TypeScript program: ${file}`);
  return source;
}

function position(project, file, line, character) {
  const source = sourceFile(project, file);
  if (line < 1 || line > source.getLineStarts().length) throw new Error('line is out of range');
  return source.getPositionOfLineAndCharacter(line - 1, Math.max(0, character));
}

function location(fileName, span, extra) {
  const project = projectFor(fileName);
  let source;
  try {
    source = sourceFile(project, fileName);
  } catch (error) {
    source = ts.createSourceFile(fileName, fs.readFileSync(fileName, 'utf8'), ts.ScriptTarget.Latest, true);
  }
  const start = source.getLineAndCharacterOfPosition(span.start);
  const end = source.getLineAndCharacterOfPosition(span.start + span.length);
  const lineText = source.text.slice(source.getLineStarts()[start.line], source.getLineEndOfPosition(span.start));
  return {
    file: fileName,
    line: start.line + 1,
    character: start.character,
    end_line: end.line + 1,
    end_character: end.character,
    text: lineText.trim().slice(0, 200),
    external: !inside(fileName) || fileName.includes(`${path.sep}node_modules${path.sep}`),
    ...extra,
  };
}

function text(parts) {
  return (parts || []).map((part) => part.text).join('');
}

const handlers = {
  ping() {
    return { version: ts.version };
  },
  definition({ file, line, character }) {
    const project = projectFor(file);
    const pos = position(project, file, line, character);
    const definitions = project.service.getDefinitionAtPosition(file, pos) || [];
    const info = project.service.getQuickInfoAtPosition(file, pos);
    return {
      definitions: definitions.slice(0, 50).map((item) => location(item.fileName, item.textSpan, {
        kind: item.kind, name: item.name, container: item.containerName || null,
      })),
      hover: info ? { signature: text(info.displayParts), doc: text(info.documentation), kind: info.kind } : null,
    };
  },
  references({ file, line, character }) {
    const project = projectFor(file);
    const pos = position(project, file, line, character);
    const symbols = project.service.findReferences(file, pos) || [];
    const result = [];
    for (const symbol of symbols) {
      for (const reference of symbol.references) {
        if (result.length >= MAX_RESULTS) break;
        result.push(location(reference.fileName, reference.textSpan, {
          definition: Boolean(reference.isDefinition), write: Boolean(reference.isWriteAccess),
          symbol: symbol.definition ? symbol.definition.name : null,
        }));
      }
    }
    return { references: result, truncated: result.length >= MAX_RESULTS };
  },
  rename({ file, line, character }) {
    const project = projectFor(file);
    const pos = position(project, file, line, character);
    const info = project.service.getRenameInfo(file, pos, { allowRenameOfImportPath: false });
    if (!info.canRename) return { can_rename: false, error: info.localizedErrorMessage };
    const locations = project.service.findRenameLocations(file, pos, false, false, { providePrefixAndSuffixTextForRename: true }) || [];
    return {
      can_rename: true,
      name: info.displayName,
      kind: info.kind,
      locations: locations.map((item) => location(item.fileName, item.textSpan, {
        prefix: item.prefixText || '', suffix: item.suffixText || '',
      })),
    };
  },
  symbols({ file }) {
    const project = projectFor(file);
    const tree = project.service.getNavigationTree(file);
    const result = [];
    const walk = (node, container) => {
      for (const child of node.childItems || []) {
        if (result.length >= 500) return;
        const span = child.nameSpan || child.spans[0];
        if (child.kind !== 'alias') {
          const item = location(file, span, { name: child.text, kind: child.kind, container });
          const whole = child.spans[0];
          item.start_line = location(file, { start: whole.start, length: 0 }).line;
          item.end_line = location(file, { start: whole.start + whole.length, length: 0 }).line;
          result.push(item);
        }
        walk(child, child.text);
      }
    };
    walk(tree, null);
    return { symbols: result };
  },
  workspace_symbols({ query, file }) {
    const project = file ? projectFor(file) : (projects.values().next().value || null);
    if (!project) return { symbols: [] };
    const items = project.service.getNavigateToItems(query, 200, undefined, true) || [];
    return {
      symbols: items.filter((item) => inside(item.fileName) && !item.fileName.includes(`${path.sep}node_modules${path.sep}`))
        .map((item) => location(item.fileName, item.textSpan, {
          name: item.name, kind: item.kind, container: item.containerName || null, match: item.matchKind,
        })),
    };
  },
  diagnostics({ files }) {
    const result = [];
    for (const file of files) {
      const project = projectFor(file);
      const items = [
        ...project.service.getSyntacticDiagnostics(file),
        ...project.service.getSemanticDiagnostics(file),
      ];
      for (const item of items) {
        if (result.length >= 300) break;
        const where = item.file && item.start !== undefined
          ? location(item.file.fileName, { start: item.start, length: item.length || 0 }) : { file };
        result.push({
          ...where,
          code: item.code,
          category: ts.DiagnosticCategory[item.category].toLowerCase(),
          message: ts.flattenDiagnosticMessageText(item.messageText, '\n').slice(0, 500),
        });
      }
    }
    return { diagnostics: result };
  },
  extract({ file, line, character, end_line, end_character, scope }) {
    const project = projectFor(file);
    const start = position(project, file, line, character);
    const end = position(project, file, end_line, end_character);
    const range = { pos: start, end };
    const preferences = { allowTextChangesInNewFiles: false, quotePreference: 'auto' };
    const refactors = project.service.getApplicableRefactors(file, range, preferences) || [];
    const extract = refactors.find((item) => item.name === 'Extract Symbol');
    const actions = extract ? extract.actions.filter((item) => item.name.startsWith('function_scope_') && !item.notApplicableReason) : [];
    if (!actions.length) {
      const reasons = extract ? extract.actions.map((item) => item.notApplicableReason).filter(Boolean) : [];
      return { ok: false, error: reasons[0] || 'the selection cannot be extracted into a function' };
    }
    // по умолчанию - самая внешняя область (функция модуля), иначе выбранная по номеру
    const action = scope === 'inner' ? actions[0] : actions[actions.length - 1];
    const format = ts.getDefaultFormatCodeSettings('\n');
    const edits = project.service.getEditsForRefactor(file, format, range, 'Extract Symbol', action.name, preferences);
    if (!edits) return { ok: false, error: 'TypeScript returned no edits' };
    return {
      ok: true,
      description: action.description,
      edits: edits.edits.map((change) => ({
        file: change.fileName,
        changes: change.textChanges.map((item) => ({ start: item.span.start, length: item.span.length, text: item.newText })),
      })),
      rename: edits.renameLocation === undefined ? null : { file: edits.renameFilename || file, offset: edits.renameLocation },
    };
  },
};

const input = readline.createInterface({ input: process.stdin });
input.on('line', (line) => {
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    return;
  }
  let response;
  try {
    const handler = handlers[request.op];
    if (!handler) throw new Error(`unknown operation ${request.op}`);
    const args = request.args || {};
    if (args.file) {
      args.file = path.resolve(root, args.file);
      if (!inside(args.file)) throw new Error('file is outside the project');
    }
    if (args.files) {
      args.files = args.files.map((item) => path.resolve(root, item));
      if (args.files.some((item) => !inside(item))) throw new Error('file is outside the project');
    }
    response = { id: request.id, ok: true, result: handler(args) };
  } catch (error) {
    response = { id: request.id, ok: false, error: String(error && error.message || error).slice(0, 500) };
  }
  process.stdout.write(JSON.stringify(response) + '\n');
});
input.on('close', () => process.exit(0));
