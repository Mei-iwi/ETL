"""Real tokenizer smoke test for Windows/Python and the Linux Docker build."""
import json
import platform

from data_sync_etl.domain.lexical import ANALYZER_VERSION, analyze, lexical_document


def main():
    tokens = analyze('Đại học dạy học máy, C++ C# .NET Python3 3.12 không giám sát')
    assert {'học', 'máy', 'không', 'c++', 'c#', '.net', 'python3', '3.12'} <= set(tokens)
    assert any('_' in token for token in tokens)
    document = lexical_document(['học không giám sát'])
    assert document.analyzer == ANALYZER_VERSION
    print(json.dumps({'python': platform.python_version(), 'system': platform.system(),
                      'analyzer': ANALYZER_VERSION, 'status': 'passed'}, ensure_ascii=True))


if __name__ == '__main__':
    main()
