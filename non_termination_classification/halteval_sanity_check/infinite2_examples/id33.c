struct _xmlLink
{
  struct _xmlLink *next;
  struct _xmlLink *prev;
  void *data;
};

typedef struct _xmlLink *xmlLinkPtr;

struct _xmlList
{
  xmlLinkPtr sentinel;
  void (*linkDeallocator)(xmlLinkPtr );
  int (*linkCompare)(const void *, const void*);
};

typedef struct _xmlList xmlList;

int id33(xmlList *l)
{
  xmlLinkPtr lk;
  int count=0;

  if (l == 0)
    return(-1);
  /* TODO: keep a counter in xmlList instead */
  for(lk = l->sentinel->next; lk != l->sentinel; lk = lk->next, count++);
  return count;
}