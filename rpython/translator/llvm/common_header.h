#ifdef _WIN64
   typedef          __int64 Signed;
   typedef unsigned __int64 Unsigned;
#  define SIGNED_MIN LLONG_MIN 
#else
   typedef          long Signed;
   typedef unsigned long Unsigned;
#  define SIGNED_MIN LONG_MIN
#endif

#define PYPY_STANDALONE