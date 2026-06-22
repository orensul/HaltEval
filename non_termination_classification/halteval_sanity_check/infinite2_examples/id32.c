unsigned id32(const char *name) {
  unsigned hashValue = 5381;
  const char *ptr;

  for (ptr = name; *ptr; ptr++)
    hashValue = hashValue * 33 + *ptr;

  return(hashValue);
}