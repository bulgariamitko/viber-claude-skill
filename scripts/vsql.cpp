// vsql: run SQL statements (one per stdin line) against Viber's encrypted viber.db, using
// Viber's own Qt SQLite plugin (which contains its codec). Prints "OK"/"FAIL" per line.
// Usage: QT_PLUGIN_PATH=/Applications/Viber.app/Contents/PlugIns vsql <viber.db> <keyfile> < stmts
// The keyfile is a JSON list of hexkey strings captured by capture_key.sh.
#include <dlfcn.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <iostream>
#include <fstream>
#include <sstream>

struct QS { void* a; void* b; long long c; };   // QString layout (Qt6), >16B => sret
struct DB { void* d; void* p1; void* p2; };     // forces sret like the real (non-trivial) class
struct QQ { void* d; void* pad[3]; };

typedef QS   (*mk_t)(long long, const char*);
typedef bool (*ex_t)(void*, QS*);
typedef DB   (*add_t)(const void*, const void*);
typedef void (*setn_t)(void*, const void*);
typedef bool (*open_t)(void*);
typedef void (*qqc_t)(void*, const void*);

static void* sym(void* h, const char* n) {
  void* p = dlsym(h, n);
  if (!p) { fprintf(stderr, "missing symbol %s\n", n); exit(3); }
  return p;
}

int main(int argc, char** argv) {
  if (argc < 3) { fprintf(stderr, "usage: vsql viber.db keyfile < stmts\n"); return 2; }
  const char* fw = "/Applications/Viber.app/Contents/Frameworks/";
  std::string core = std::string(fw) + "QtCore.framework/Versions/A/QtCore";
  std::string sql  = std::string(fw) + "QtSql.framework/Versions/A/QtSql";
  void* hc = dlopen(core.c_str(), RTLD_NOW | RTLD_GLOBAL);
  void* hs = dlopen(sql.c_str(),  RTLD_NOW | RTLD_GLOBAL);
  if (!hc || !hs) { fprintf(stderr, "dlopen failed: %s\n", dlerror()); return 3; }

  mk_t   mk   = (mk_t)  sym(hc, "_ZN7QString8fromUtf8E14QByteArrayView");
  ex_t   ex   = (ex_t)  sym(hs, "_ZN9QSqlQuery4execERK7QString");
  add_t  add  = (add_t) sym(hs, "_ZN12QSqlDatabase11addDatabaseERK7QStringS2_");
  setn_t setn = (setn_t)sym(hs, "_ZN12QSqlDatabase15setDatabaseNameERK7QString");
  open_t opn  = (open_t)sym(hs, "_ZN12QSqlDatabase4openEv");
  qqc_t  qqc  = (qqc_t) sym(hs, "_ZN9QSqlQueryC1ERK12QSqlDatabase");

  // QSqlDatabase requires a QCoreApplication instance.
  typedef void (*app_t)(void*, int&, char**, int);
  app_t appc = (app_t)sym(hc, "_ZN16QCoreApplicationC1ERiPPci");
  static int qargc = 1; static char* qargv[] = {(char*)"vsql", nullptr};
  alignas(16) static unsigned char appbuf[4096]; appc(appbuf, qargc, qargv, 0x060b00);

  auto S = [&](const std::string& s) { return mk((long long)s.size(), s.c_str()); };

  QS tp = S("QSQLITE"), cn = S("vsql_conn");
  DB db = add(&tp, &cn);
  QS pth = S(argv[1]); setn(&db, &pth);
  if (!opn(&db)) { fprintf(stderr, "open failed\n"); return 4; }
  QQ q; memset(&q, 0, sizeof q); qqc(&q, &db);

  // keys: crude JSON list parse ["..","..."]
  std::ifstream kf(argv[2]); std::stringstream ks; ks << kf.rdbuf(); std::string kj = ks.str();
  std::vector<std::string> keys; size_t i = 0;
  while ((i = kj.find('"', i)) != std::string::npos) {
    size_t j = kj.find('"', i + 1); if (j == std::string::npos) break;
    keys.push_back(kj.substr(i + 1, j - i - 1)); i = j + 1;
  }
  for (auto& k : keys) { QS s = S("PRAGMA hexkey='" + k + "'"); ex(&q, &s); }
  QS probe = S("SELECT count(*) FROM sqlite_master");
  if (!ex(&q, &probe)) { fprintf(stderr, "decrypt failed (wrong key?)\n"); return 5; }

  std::string line; int fails = 0;
  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;
    QS s = S(line); bool ok = ex(&q, &s);
    printf("%s\t%s\n", ok ? "OK" : "FAIL", line.substr(0, 120).c_str());
    if (!ok) fails++;
  }
  fflush(stdout);
  _exit(fails ? 1 : 0);   // skip Qt teardown
}
