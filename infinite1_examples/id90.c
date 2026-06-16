void id90(int val){
  int num;
  int i;
  int p = 0;

  if (val == 0)
    num = 1;
  else
    num = 10;

  for (i = 0; i < num; i++)
    p += val;
}