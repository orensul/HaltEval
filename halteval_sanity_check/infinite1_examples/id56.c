typedef struct s_list {
  int value;
  struct s_list* next;
} list_t;

static void id56(list_t* p){
  int tot = 0;
  do {
    tot += p->value;
    p = p->next;
  } while (p != 0);
}